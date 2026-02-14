import os
import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import mention_html
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
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
    """Fetches and displays ticket categories."""
    
    # Access Control
    user = update.message.from_user
    if user.username != "amfares13":
        await update.message.reply_text("⛔ You are not authorized to view tickets.")
        return ConversationHandler.END

    driver = get_db_driver()
    
    try:
        with driver.session() as session:
            # Get ticket counts by feature
            feature_query = """
            MATCH (t:Ticket {status: 'Open'})
            RETURN t.feature as feature, count(t) as count
            ORDER BY count DESC
            """
            result = session.run(feature_query)
            features = [record.data() for record in result]
            
            # Get total stats
            stats_query = """
            MATCH (t:Ticket)
            WITH count(t) as total
            MATCH (open:Ticket {status: 'Open'})
            RETURN total, count(open) as open_count
            """
            stats_result = session.run(stats_query)
            stats = stats_result.single()
            total = stats['total'] if stats else 0
            open_count = stats['open_count'] if stats else 0
        
        if not features:
            await update.message.reply_text(
                f"🎉 No open tickets found! Everything seems to be working smoothly.\n\n"
                f"📊 Stats: {total} total tickets, {open_count} open"
            )
        else:
            # Create inline keyboard with feature categories
            keyboard = []
            for feature_data in features:
                feature = feature_data['feature']
                count = feature_data['count']
                keyboard.append([InlineKeyboardButton(
                    f"{feature} ({count})", 
                    callback_data=f"category:{feature}"
                )])
            
            # Add "All Tickets" button
            keyboard.append([InlineKeyboardButton(
                f"📋 All Tickets ({open_count})", 
                callback_data="category:ALL"
            )])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"📊 *Open Tickets by Category*\n\n"
                f"Total: {total} tickets ({open_count} open)\n\n"
                f"Select a category to view tickets:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Error fetching ticket categories: {e}")
        await update.message.reply_text("Sorry, I couldn't retrieve the tickets right now.")
    finally:
        driver.close()

    return ConversationHandler.END

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle category selection and show tickets as buttons."""
    query = update.callback_query
    await query.answer()
    
    category = query.data.split(":")[1]
    
    driver = get_db_driver()
    
    try:
        with driver.session() as session:
            # Get tickets for selected category
            if category == "ALL":
                tickets_query = """
                MATCH (u:User)-[:REPORTED]->(t:Ticket {status: 'Open'})
                RETURN t.id AS id, t.feature AS feature, t.course_code AS course_code,
                       t.description AS description, t.created_at AS created_at,
                       u.telegram_id AS user_id, u.username AS username, u.first_name AS first_name
                ORDER BY t.created_at DESC
                """
                result = session.run(tickets_query)
            else:
                tickets_query = """
                MATCH (u:User)-[:REPORTED]->(t:Ticket {status: 'Open', feature: $feature})
                RETURN t.id AS id, t.feature AS feature, t.course_code AS course_code,
                       t.description AS description, t.created_at AS created_at,
                       u.telegram_id AS user_id, u.username AS username, u.first_name AS first_name
                ORDER BY t.created_at DESC
                """
                result = session.run(tickets_query, feature=category)
            
            tickets = [record.data() for record in result]
        
        if not tickets:
            await query.edit_message_text(
                f"No open tickets found in category: {category}"
            )
        else:
            # Create inline keyboard with ticket buttons
            keyboard = []
            for ticket in tickets[:20]:  # Limit to 20 tickets to avoid message size limits
                ticket_id_short = ticket['id'][:8]
                course = f" [{ticket['course_code']}]" if ticket.get('course_code') else ""
                description_preview = ticket['description'][:30] + "..." if len(ticket['description']) > 30 else ticket['description']
                
                button_text = f"🎫 {ticket_id_short}: {description_preview}"
                keyboard.append([InlineKeyboardButton(
                    button_text,
                    callback_data=f"ticket:{ticket['id']}"
                )])
            
            # Add back button
            keyboard.append([InlineKeyboardButton("⬅️ Back to Categories", callback_data="back_to_categories")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            title = f"All Tickets" if category == "ALL" else category
            await query.edit_message_text(
                f"📋 *{title}* ({len(tickets)} open)\n\n"
                f"Select a ticket to view details:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Error fetching tickets for category: {e}")
        await query.edit_message_text("Sorry, I couldn't retrieve the tickets right now.")
    finally:
        driver.close()

async def ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ticket selection and show ticket details with close button."""
    query = update.callback_query
    await query.answer()
    
    ticket_id = query.data.split(":")[1]
    
    driver = get_db_driver()
    
    try:
        with driver.session() as session:
            # Get ticket details
            ticket_query = """
            MATCH (u:User)-[:REPORTED]->(t:Ticket {id: $ticket_id})
            RETURN t.id AS id, t.feature AS feature, t.course_code AS course_code,
                   t.description AS description, t.created_at AS created_at, t.status AS status,
                   u.telegram_id AS user_id, u.username AS username, u.first_name AS first_name
            """
            result = session.run(ticket_query, ticket_id=ticket_id)
            ticket = result.single()
            
            if not ticket:
                await query.edit_message_text("Ticket not found.")
                return
            
            ticket_data = ticket.data()
            
            # Format ticket details
            ticket_id_short = ticket_data['id'][:8]
            date_str = ticket_data['created_at'].split("T")[0] if ticket_data.get('created_at') else "N/A"
            course = f"\n<b>Course:</b> {ticket_data['course_code']}" if ticket_data.get('course_code') else ""
            
            # Create user mention
            username = ticket_data.get('username')
            user_id = ticket_data.get('user_id')
            first_name = ticket_data.get('first_name', 'User')
            
            if username:
                user_display = f"@{username}"
            elif user_id:
                user_display = mention_html(user_id, first_name)
            else:
                user_display = "Unknown User"
            
            message = (
                f"🎫 <b>Ticket Details</b>\n\n"
                f"<b>ID:</b> <code>{ticket_id_short}</code>\n"
                f"<b>Feature:</b> {ticket_data['feature']}{course}\n"
                f"<b>Status:</b> {ticket_data['status']}\n"
                f"<b>Reported by:</b> {user_display}\n"
                f"<b>Date:</b> {date_str}\n\n"
                f"<b>Description:</b>\n{ticket_data['description']}"
            )
            
            # Create keyboard with close button
            keyboard = []
            if ticket_data['status'] == 'Open':
                keyboard.append([InlineKeyboardButton("✅ Close Ticket", callback_data=f"close:{ticket_id}")])
            keyboard.append([InlineKeyboardButton("⬅️ Back to List", callback_data=f"category:{ticket_data['feature']}")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            
    except Exception as e:
        logger.error(f"Error fetching ticket details: {e}")
        await query.edit_message_text("Sorry, I couldn't retrieve the ticket details.")
    finally:
        driver.close()

async def close_ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle closing a ticket."""
    query = update.callback_query
    await query.answer("Closing ticket...")
    
    ticket_id = query.data.split(":")[1]
    
    driver = get_db_driver()
    
    try:
        with driver.session() as session:
            # Update ticket status to Closed
            close_query = """
            MATCH (t:Ticket {id: $ticket_id})
            SET t.status = 'Closed', t.closed_at = $closed_at
            RETURN t.feature AS feature
            """
            result = session.run(close_query, ticket_id=ticket_id, closed_at=datetime.now().isoformat())
            record = result.single()
            
            if record:
                feature = record['feature']
                ticket_id_short = ticket_id[:8]
                
                await query.edit_message_text(
                    f"✅ Ticket <code>{ticket_id_short}</code> has been closed successfully!",
                    parse_mode='HTML'
                )
                
                # Optionally notify the user who reported it
                # (You can add this feature if needed)
            else:
                await query.edit_message_text("Ticket not found or already closed.")
                
    except Exception as e:
        logger.error(f"Error closing ticket: {e}")
        await query.edit_message_text("Sorry, I couldn't close the ticket.")
    finally:
        driver.close()

async def back_to_categories_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back to categories button."""
    query = update.callback_query
    await query.answer()
    
    driver = get_db_driver()
    
    try:
        with driver.session() as session:
            # Get ticket counts by feature
            feature_query = """
            MATCH (t:Ticket {status: 'Open'})
            RETURN t.feature as feature, count(t) as count
            ORDER BY count DESC
            """
            result = session.run(feature_query)
            features = [record.data() for record in result]
            
            # Get total stats
            stats_query = """
            MATCH (t:Ticket)
            WITH count(t) as total
            MATCH (open:Ticket {status: 'Open'})
            RETURN total, count(open) as open_count
            """
            stats_result = session.run(stats_query)
            stats = stats_result.single()
            total = stats['total'] if stats else 0
            open_count = stats['open_count'] if stats else 0
        
        # Create inline keyboard with feature categories
        keyboard = []
        for feature_data in features:
            feature = feature_data['feature']
            count = feature_data['count']
            keyboard.append([InlineKeyboardButton(
                f"{feature} ({count})", 
                callback_data=f"category:{feature}"
            )])
        
        # Add "All Tickets" button
        keyboard.append([InlineKeyboardButton(
            f"📋 All Tickets ({open_count})", 
            callback_data="category:ALL"
        )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"📊 *Open Tickets by Category*\n\n"
            f"Total: {total} tickets ({open_count} open)\n\n"
            f"Select a category to view tickets:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
            
    except Exception as e:
        logger.error(f"Error returning to categories: {e}")
        await query.edit_message_text("Sorry, something went wrong.")
    finally:
        driver.close()

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
        
        # Send confirmation to user
        await update.message.reply_text(
            f"✅ Ticket created successfully! Thank you for your feedback.\n"
            f"Ticket ID: `{ticket_id[:8]}`",
            parse_mode='Markdown'
        )
        
        # Send notification to admin (@amfares13)
        admin_username = "amfares13"
        
        # Get admin's telegram ID from database
        admin_query = "MATCH (u:User {username: $username}) RETURN u.telegram_id as telegram_id"
        with driver.session() as session:
            admin_result = session.run(admin_query, username=admin_username)
            admin_record = admin_result.single()
            
            if admin_record and admin_record['telegram_id']:
                admin_id = admin_record['telegram_id']
                
                # Build notification message
                course_info = f" [{course_code}]" if course_code else ""
                
                # Create user mention for the reporter
                if user.username:
                    reporter_display = f"@{user.username}"
                else:
                    reporter_display = mention_html(user.id, user.first_name)
                
                notification = (
                    f"🚨 <b>New Bug Report</b>\n\n"
                    f"<b>Feature:</b> {feature}{course_info}\n"
                    f"<b>Description:</b> {description}\n"
                    f"<b>Reported by:</b> {reporter_display}\n"
                    f"<b>Ticket ID:</b> <code>{ticket_id[:8]}</code>"
                )
                
                # Send notification to admin
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=notification,
                    parse_mode='HTML'
                )
            else:
                logger.warning(f"Admin {admin_username} not found in database or no telegram_id")
        
        # Return to main menu
        reply_keyboard = [["Report a Bug", "View Open Tickets"]]
        await update.message.reply_text(
            "How can I help you today?",
            reply_markup=ReplyKeyboardMarkup(
                reply_keyboard, one_time_keyboard=True, resize_keyboard=True
            ),
        )
    except Exception as e:
        logger.error(f"Error saving ticket or sending notification: {e}")
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
    
    # Add callback query handlers for interactive buttons
    application.add_handler(CallbackQueryHandler(category_callback, pattern="^category:"))
    application.add_handler(CallbackQueryHandler(ticket_callback, pattern="^ticket:"))
    application.add_handler(CallbackQueryHandler(close_ticket_callback, pattern="^close:"))
    application.add_handler(CallbackQueryHandler(back_to_categories_callback, pattern="^back_to_categories$"))

    logger.info("🚀 Bot starting with Neo4j database...")
    logger.info(f"📊 Database URI: {URI}")
    
    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()