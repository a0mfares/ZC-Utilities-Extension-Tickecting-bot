# ZC Toolbox Support Bot 🤖

This Telegram bot allows users to report bugs and view open tickets for the ZC Toolbox. It connects to a Neo4j Aura database to store tickets.

## 🚀 Setup

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Environment Variables**:
    - Copy `.env.example` to `.env`:
        ```bash
        cp .env.example .env
        ```
    - Open `.env` and fill in your details:
        - `TELEGRAM_BOT_TOKEN`: Get this from [BotFather](https://t.me/BotFather).
        - `NEO4J_URI`: Your Neo4j Aura connection URI (e.g., `neo4j+s://...`).
        - `NEO4J_USERNAME`: Usually `neo4j`.
        - `NEO4J_PASSWORD`: Your database password.

3.  **Run the Bot**:
    ```bash
    python bot.py
    ```

## 🛠️ Features

-   **Report a Bug**:
    -   Select from features like **Planner**, **GPA**, etc.
    -   **Planner Specifics**: Prompts for Course Code + Issue Description for better tracking.
-   **View Open Tickets**:
    -   Lists the latest 10 open tickets directly from the database.

## 🗄️ Database Schema (Neo4j)

The bot creates `Ticket` nodes with relationships to `User` nodes.

-   `(:User)-[:REPORTED]->(:Ticket)`
-   **Ticket Properties**: `feature`, `description`, `course_code`, `status`, `created_at`
