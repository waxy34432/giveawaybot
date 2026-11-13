# Telegram Giveaway Bot — inline admin panel

Python 3.11+ / aiogram 3 / SQLite.

## Setup
1. Upload the contents of this folder to Python hosting.
2. Copy `.env.example` to `.env`; fill `BOT_TOKEN` and `OWNER_ID`. To add more admins, put their numeric Telegram IDs in `ADMIN_IDS`, separated by commas.
3. Install `requirements.txt`.
4. Start: `python bot.py`.
5. Add the bot as administrator to both required channels.

## Controls
Send `/start` once to open the admin panel. The owner (`OWNER_ID`) and every ID in `ADMIN_IDS` have admin access. Payment receipt notifications are sent to all configured admins. After that, admin navigation and actions are inline buttons:
- Create giveaway
- My giveaways
- Statistics
- Publish draft
- Approve/reject payment
- Draw winner

The bot still asks for text/photo input while creating a giveaway and for the number of tickets / receipt during participation. These are data-entry messages, not bot commands.

## MVP limits
This is an MVP. Publishing currently targets the first channel; both channels are checked for required membership. Editing/removing posts, automatic end time, manual winner selection, exports, and advanced fraud controls are not included. Verify payments in the receiving account before approving receipts. Check the law and hosting/payment-provider terms for paid prize draws before public use. Use persistent storage and back up SQLite.
