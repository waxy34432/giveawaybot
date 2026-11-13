# Telegram Giveaway Bot (MVP)

Python 3.11+ / aiogram 3 / SQLite. Admin works in private chat.

## Included
- Owner-only `/admin` panel
- Create draft giveaway through chat prompts
- Two required channel subscriptions checked via `get_chat_member`
- Publish post with participation button
- Ticket quantity and manual payment instructions
- Receipt intake (photo/PDF) sent to owner
- Manual approve/reject; tickets issued only once after approval
- Unique random ticket numbers
- Random winner draw with saved history
- Basic statistics

## Setup
1. Create a bot with BotFather.
2. Add the bot as administrator to both channels. It must be able to access member status; test with a real non-admin user.
3. Upload all files to your Python hosting.
4. Copy `.env.example` to `.env` and fill `BOT_TOKEN` and `OWNER_ID`.
5. Install dependencies from `requirements.txt`.
6. Start with `python bot.py`.

Do not publish `.env` or share your bot token.

## Admin commands
- `/admin` — show admin menu
- `/draw ID` — draw a winner for a published giveaway
- `/cancel` — cancel current creation/payment flow

## Important limitations before public launch
This is an MVP, not a production-certified raffle platform. Verify that paid prize draws are lawful in the countries where organizer and participants are located. Check hosting/payment-provider rules. A screenshot is not proof that funds arrived: verify the payment in the receiving account before approving it.

The current MVP publishes to the first channel only; both channels are used for subscription checks. It does not yet include editing/removing published posts, scheduled closing, multiple admins, or a full participant export. Back up the SQLite database regularly and use persistent storage on hosting.
