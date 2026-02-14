import os
import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from neo4j import GraphDatabase
from dotenv import load_dotenv
import uuid
from datetime import datetime

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Neo4j Setup
URI = os.getenv("NEO4J_URI")
AUTH = (os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))

def get_db_driver():
    return GraphDatabase.driver(URI, auth=AUTH)

# States
SELECT_FEATURE, INPUT_ISSUE = range(2)

# Features
FEATURES = ["Stop List", "GPA", "Coursework", "Planner", "Others"]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and asks the user what they want to do."""
    user = update.message.from_user
    
    # Save/update user in database
    driver = get_db_driver()
    try:
        with driver.session() as session:
            session.run("""
                MERGE (u:User {telegram_id: $user_id})
                ON CREATE SET u.created_at = $created_at
                SET u.username = $username, 
                    u.first_name = $first_name, 
                    u.last_activity = $last_activity
            """, 
                user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                created_at=datetime.now().isoformat(),
                last_activity=datetime.now().isoformat()
            )
    except Exception as e:
        logger.error(f"Error saving user: {e}")
    finally:
        driver.close()
    
    reply_keyboard = [["Report a Bug", "View Open Tickets"]]
    await update.message.reply_text(
        "Welcome to the ZC Toolbox Support Bot! 🛠️\n"
        "How can I help you today?",
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return ConversationHandler.END

async def report_bug_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the bug reporting flow."""
    # Arrange features in 2 columns (custom order)
    reply_keyboard = [
        ["Stop List", "GPA"],
        ["Coursework", "Planner"],
        ["Others"]
    ]
    await update.message.reply_text(
        "Please select the feature where you encountered the bug:",
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return SELECT_FEATURE


async def view_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Fetches and displays open tickets."""
    
    # Access Control
    user = update.message.from_user
    if user.username != "amfares13":
        await update.message.reply_text("⛔ You are not authorized to view tickets.")
        return ConversationHandler.END

    driver = get_db_driver()
    
    # Get total and open count
    stats_query = """
    MATCH (t:Ticket)
    WITH count(t) as total
    MATCH (open:Ticket {status: 'Open'})
    RETURN total, count(open) as open_count
    """
    
    # Get open tickets
    tickets_query = """
    MATCH (u:User)-[:REPORTED]->(t:Ticket {status: 'Open'})
    RETURN t.feature AS feature, t.course_code AS course_code, 
           t.description AS description, t.created_at AS created_at,
           u.telegram_id AS user_id, u.username AS username, u.first_name AS first_name
    ORDER BY t.created_at DESC
    LIMIT 10
    """
    
    try:
        with driver.session() as session:
            # Get stats
            stats_result = session.run(stats_query)
            stats = stats_result.single()
            total = stats['total'] if stats else 0
            open_count = stats['open_count'] if stats else 0
            
            # Get tickets
            result = session.run(tickets_query)
            tickets = [record.data() for record in result]
            
        if not tickets:
            await update.message.reply_text(
                f"🎉 No open tickets found! Everything seems to be working smoothly.\n\n"
                f"📊 Stats: {total} total tickets, {open_count} open"
            )
        else:
            response = f"📋 *Open Tickets* ({len(tickets)} of {open_count}):\n\n"
            for t in tickets:
                date_str = t['created_at'].split("T")[0] if t.get('created_at') else "N/A"
                course = f" [{t['course_code']}]" if t.get('course_code') else ""
                
                # Display username with @ for easy contact
                username = t.get('username')
                if username:
                    user_display = f"@{username}"
                else:
                    # Fallback to user ID if no username
                    user_id = t.get('user_id', 'Unknown')
                    user_display = f"User ID: {user_id}"
                
                response += f"🔹 *{t['feature']}*{course}\n"
                response += f"   {t['description']}\n"
                response += f"   _By {user_display} on {date_str}_\n\n"
            
            await update.message.reply_text(response, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error fetching tickets: {e}")
        await update.message.reply_text("Sorry, I couldn't retrieve the tickets right now.")
    finally:
        driver.close()

    return ConversationHandler.END

async def select_feature(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the selected feature and asks for the issue description."""
    feature = update.message.text
    context.user_data["feature"] = feature

    if feature == "Planner":
        await update.message.reply_text(
            "Please describe the issue in the following format:\n\n"
            "Line 1: *Course Code* (e.g., CSEN101)\n"
            "Line 2+: *Description of the problem*\n\n"
            "Example:\n"
            "CSEN102\n"
            "The prerequisites shown are incorrect.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"Please describe the bug you found in *{feature}*.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode='Markdown'
        )

    return INPUT_ISSUE

async def input_issue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the issue and saves it to Neo4j."""
    text = update.message.text
    feature = context.user_data["feature"]
    user = update.message.from_user
    
    course_code = None
    description = text

    if feature == "Planner":
        lines = text.split('\n', 1)
        if len(lines) >= 1:
             # Basic attempt to extract course code from first line
            possible_code = lines[0].strip().upper()
            # If the user followed instructions, the first line is the code
            # and the rest is description.
            if len(lines) > 1:
                course_code = possible_code
                description = lines[1].strip()
            else:
                 # If only one line, assume it's just description or they forgot formatting
                 # We'll just save it all as description if we can't be sure
                 pass
    
    # Save to Neo4j
    driver = get_db_driver()
    ticket_id = str(uuid.uuid4())
    query = """
    MERGE (u:User {telegram_id: $user_id})
    ON CREATE SET u.created_at = $created_at
    SET u.username = $username, 
        u.first_name = $first_name, 
        u.last_activity = $last_activity
    CREATE (t:Ticket {
        id: $ticket_id,
        feature: $feature,
        course_code: $course_code,
        description: $description,
        status: 'Open',
        created_at: $created_at
    })
    MERGE (u)-[:REPORTED]->(t)
    """
    
    try:
        with driver.session() as session:
            session.run(query, 
                        user_id=user.id, 
                        username=user.username, 
                        first_name=user.first_name,
                        ticket_id=ticket_id,
                        feature=feature,
                        course_code=course_code,
                        description=description,
                        created_at=datetime.now().isoformat(),
                        last_activity=datetime.now().isoformat()
            )
        await update.message.reply_text(
            f"✅ Ticket created successfully! Thank you for your feedback.\n"
            f"Ticket ID: `{ticket_id[:8]}`",
            parse_mode='Markdown'
        )
        
        # Return to main menu
        reply_keyboard = [["Report a Bug", "View Open Tickets"]]
        await update.message.reply_text(
            "How can I help you today?",
            reply_markup=ReplyKeyboardMarkup(
                reply_keyboard, one_time_keyboard=True, resize_keyboard=True
            ),
        )
    except Exception as e:
        logger.error(f"Error saving ticket: {e}")
        await update.message.reply_text("❌ There was an error saving your ticket. Please try again later.")
    finally:
        driver.close()

    context.user_data.clear()
    return ConversationHandler.END

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show ticket statistics (admin only)"""
    user = update.message.from_user
    if user.username != "amfares13":
        await update.message.reply_text("⛔ You are not authorized to view statistics.")
        return ConversationHandler.END
    
    driver = get_db_driver()
    
    try:
        with driver.session() as session:
            # Get overall stats
            overall_query = """
            MATCH (t:Ticket)
            WITH count(t) as total
            MATCH (open:Ticket {status: 'Open'})
            WITH total, count(open) as open_count
            MATCH (closed:Ticket {status: 'Closed'})
            RETURN total, open_count, count(closed) as closed_count
            """
            overall = session.run(overall_query).single()
            total = overall['total'] if overall else 0
            open_count = overall['open_count'] if overall else 0
            closed_count = overall['closed_count'] if overall else 0
            
            # Get feature breakdown
            feature_query = """
            MATCH (t:Ticket)
            RETURN t.feature as feature, count(t) as count
            ORDER BY count DESC
            """
            features = session.run(feature_query)
            feature_counts = {record['feature']: record['count'] for record in features}
            
            # Get user count
            user_query = "MATCH (u:User) RETURN count(u) as user_count"
            user_count = session.run(user_query).single()['user_count']
        
        response = "📊 *Ticket Statistics*\n\n"
        response += f"Total Tickets: {total}\n"
        response += f"🟢 Open: {open_count}\n"
        response += f"🔴 Closed: {closed_count}\n"
        response += f"👥 Users: {user_count}\n\n"
        
        if feature_counts:
            response += "*By Feature:*\n"
            for feature, count in sorted(feature_counts.items(), key=lambda x: x[1], reverse=True):
                response += f"• {feature}: {count}\n"
        
        await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        await update.message.reply_text("❌ Error retrieving statistics.")
    finally:
        driver.close()
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text(
        "Operation cancelled. Type /start to begin again.", reply_markup=ReplyKeyboardRemove()
    )
    context.user_data.clear()
    return ConversationHandler.END

def main() -> None:
    """Run the bot."""
    # Create the Application and pass it your bot's token.
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("Error: TELEGRAM_BOT_TOKEN not found in .env")
        print("Error: TELEGRAM_BOT_TOKEN not found in .env")
        return

    application = Application.builder().token(token).build()

    # Add conversation handler with the states SELECT_FEATURE and INPUT_ISSUE
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("report", report_bug_start), 
            MessageHandler(filters.Regex("^Report a Bug$"), report_bug_start)
        ],
        states={
            SELECT_FEATURE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_feature)],
            INPUT_ISSUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_issue)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(MessageHandler(filters.Regex("^View Open Tickets$"), view_tickets))
    application.add_handler(conv_handler)

    logger.info("🚀 Bot starting with Neo4j database...")
    logger.info(f"📊 Database URI: {URI}")
    
    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()