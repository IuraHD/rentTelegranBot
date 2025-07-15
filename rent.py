import requests
import time
from bs4 import BeautifulSoup
from telegram import (
    Bot, Update, ReplyKeyboardMarkup, ParseMode
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackContext, ConversationHandler
)

# ------------------------------------------------------------------
#   rentTelegranBot
#   Compatible with Python 3.11.0+
#   Make sure to install dependencies from requirements.txt
# ------------------------------------------------------------------

SCRAPE_INTERVAL = 300
# IMPORTANT: Replace 'YOUR_TELEGRAM_BOT_TOKEN_HERE' with your actual bot token.
TELEGRAM_BOT_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN_HERE'
bot = Bot(token=TELEGRAM_BOT_TOKEN)

LOCATION_IDENTIFIER = "REGION^92827" # This is for North West London, as inferred from OnTheMarket URL

# ------------------------------------------------------------------
#   Rightmove URL Template
# ------------------------------------------------------------------
# Note: We will NOT exclude 'maisonette' via URL. Instead, we
#       will do a post-processing filter in the code.
rightmove_url_template = (
    "https://www.rightmove.co.uk/property-to-rent/find.html?"
    "locationIdentifier={location_identifier}"
    "&minBedrooms=2&maxPrice=1900"
    "&radius=0.25&sortType=1"
    "&propertyTypes=bungalow%2Cdetached%2Cflat%2Cland%2Cpark-home%2Cprivate-halls%2Csemi-detached%2Cterraced"
    "&maxDaysSinceAdded={days_added}"
    "&dontShow=houseShare"
    "&furnishTypes=&keywords="
)

onthemarket_url_template = (
    "https://www.onthemarket.com/to-rent/property/north-west-london/"
    "?max-price=1900" # Corrected from '1 00' to '1900' to match Rightmove's maxPrice
    "&min-bedrooms=2"
    "&page={page}"
    "&recently-added={recently_added}"
    "&shared=false&direction=asc"
)

# Conversation States
CHOOSING_PLATFORM, CHOOSING_DAYS = range(2)

# ------------------------------------------------------------------
#   Helper: Send & Log message
# ------------------------------------------------------------------

def send_and_log_message(
    context: CallbackContext,
    chat_id: int,
    text: str,
    parse_mode=None,
    reply_markup=None,
    disable_web_page_preview=None
):
    """
    Sends a message, returns the Message object,
    and logs the message_id in user_data['msg_ids'] so we can delete later.
    """
    msg = context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview
    )
    user_data = context.user_data
    if 'msg_ids' not in user_data:
        user_data['msg_ids'] = []
    user_data['msg_ids'].append(msg.message_id)
    return msg

# ------------------------------------------------------------------
#   Scraping and Messaging
# ------------------------------------------------------------------

def scrape_rightmove_properties_with_pagination(days_added: int) -> list:
    """Scrapes Rightmove for properties with pagination."""
    print(f"[LOG] Starting Rightmove scraping for properties added in the last {days_added} days...")
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            )
        }
        
        property_list = []
        index = 0  # Initial index for pagination

        while True:
            print(f"[LOG] Fetching Rightmove properties, page index: {index}")
            url = rightmove_url_template.format(
                location_identifier=LOCATION_IDENTIFIER,
                days_added=days_added
            ) + f"&index={index}"

            response = requests.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            properties = soup.find_all('div', class_='PropertyCard_propertyCardContainer__VSRSA')

            if not properties:
                print("[LOG] No more properties found on Rightmove.")
                break

            for prop in properties:
                title_elem = prop.find('span', class_='PropertyInformation_propertyType__u8e76')
                address_elem = prop.find('address', class_='PropertyAddress_address__LYRPq')
                price_elem = prop.find('div', class_='PropertyPrice_price__VL65t')
                link_elem = prop.find('a', class_='propertyCard-link')

                title_text = title_elem.text.strip() if title_elem else "No title"
                address_text = address_elem.text.strip() if address_elem else "No address"
                price_text = price_elem.text.strip() if price_elem else "No price"

                link_href = (
                    f"https://www.rightmove.co.uk{link_elem['href']}"
                    if link_elem else "No link"
                )

                # Exclude "maisonette" titles
                if 'maisonette' in title_text.lower():
                    continue

                # Build a dictionary for each property
                property_list.append({
                    "title": title_text,
                    "address": address_text,
                    "price": price_text,
                    "link": link_href
                })

            # Increment the index for the next page
            index += 24
            time.sleep(1) # Be respectful with scraping frequency
        print(f"[LOG] Finished scraping Rightmove. Total properties found: {len(property_list)}")
        print("\n[Summary]\n" + "\n".join([
            f"- Title: {p['title']}\n  Address: {p['address']}\n  Price: {p['price']}\n  Link: {p['link']}"
            for p in property_list
        ]))
        return property_list

    except requests.exceptions.RequestException as e:
        print(f"Error occurred during Rightmove scraping: {e}")
        return []

def scrape_onthemarket_properties(recently_added: str = "24-hours") -> list:
    """Scrapes OnTheMarket for properties based on the recently added filter."""
    print(f"[LOG] Starting OnTheMarket scraping for properties added in the last {recently_added}...")
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            )
        }
        property_list = []
        page = 1
        while True:
            print(f"[LOG] Fetching OnTheMarket properties, page: {page}")
            url = onthemarket_url_template.format(page=page, recently_added=recently_added)
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Locate the <ul> with class 'grid-list-tabcontent'
            properties_container = soup.find('ul', class_='grid-list-tabcontent')
            if not properties_container:
                print("[LOG] No more properties container found on OnTheMarket.")
                break  # Exit loop if no properties container is found

            # Find all <li> elements within the container
            properties = properties_container.find_all('div', class_='otm-PropertyCardInfo')
            if not properties:
                print("[LOG] No more properties found on OnTheMarket.")
                break  # Exit loop if no properties are found

            for prop in properties:
                title_elem = prop.find('span', itemprop='name')
                address_elem = prop.find('span', class_='address')
                link_elem = prop.find('a', href=True)
                price_elem = prop.find('div',class_='otm-Price')

                title_text = title_elem.text.strip() if title_elem else "No title"
                address_text = address_elem.text.strip() if address_elem else "No address"
                price_text = price_elem.text.strip() if price_elem else "No Price"
                price_text = price_text.replace("Tenancy info", "").strip()  # Remove "Tenancy info" from the price
                link_href = f"https://www.onthemarket.com{link_elem['href']}" if link_elem else "No link"

                if 'maisonette' in title_text.lower():
                    continue
                property_list.append({
                    "title": title_text,
                    "address": address_text,
                    "price": price_text,
                    "link": link_href
                })

            page += 1
            time.sleep(1) # Be respectful with scraping frequency
        print(f"[LOG] Finished scraping OnTheMarket. Total properties found: {len(property_list)}")
        print("\n[Summary]\n" + "\n".join([
            f"- Title: {p['title']}\n  Address: {p['address']}\n  Price: {p['price']}\n  Link: {p['link']}"
            for p in property_list
        ]))
        return property_list

    except requests.exceptions.RequestException as e:
        print(f"Error occurred during OnTheMarket scraping: {e}")
        return []
    
def notify_properties(properties: list, chat_id: int, context: CallbackContext):
    """Send the scraped property list to the user (and log the messages)."""
    print(f"[LOG] Notifying user with {len(properties)} properties.")
    if not properties:
        send_and_log_message(context, chat_id, "No properties found based on your criteria.")
        return

    for prop in properties:
        # Build a Google Maps link for the address
        google_maps_link = (
            "https://www.google.com/maps/search/?api=1&query="
            + prop.get('address', '').replace(' ', '+')
        )
        message = (
            f"<b>Title:</b> <a href=\"{prop['link']}\">{prop['title']}</a>\n"
            f"<b>Address:</b> <a href=\"{google_maps_link}\">{prop['address']}</a>\n"
            f"<b>Price:</b> {prop['price']}"
        )
        send_and_log_message(
            context,
            chat_id,
            text=message,
            parse_mode=ParseMode.HTML
        )

    send_and_log_message(
        context,
        chat_id,
        text=f"A total of {len(properties)} properties were found."
    )

# ------------------------------------------------------------------
#   Conversation Steps
# ------------------------------------------------------------------

def start(update: Update, context: CallbackContext) -> int:
    """
    /start entry point.
    1) Delete old messages from previous conversation.
    2) Go to CHOOSING_PLATFORM.
    """
    chat_id = update.effective_chat.id

    print(f"[LOG] User {chat_id} started a new session.")
    # First, delete old messages if any
    if 'msg_ids' in context.user_data:
        for mid in context.user_data['msg_ids']:
            try:
                context.bot.delete_message(chat_id=chat_id, message_id=mid)
            except Exception as e:
                print(f"[LOG] Could not delete message {mid} for chat {chat_id}: {e}")
                pass # Ignore errors if message already deleted or not found
        context.user_data['msg_ids'].clear()

    reply_markup = ReplyKeyboardMarkup(
        [['Rightmove', 'OnTheMarket']],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    send_and_log_message(
        context,
        chat_id,
        "Welcome! Choose a platform to scrape from. (Or /cancel to exit.)",
        reply_markup=reply_markup
    )
    return CHOOSING_PLATFORM

def choose_platform(update: Update, context: CallbackContext) -> int:
    """User picks the platform."""
    chat_id = update.effective_chat.id
    user_choice = update.message.text.strip().lower()

    print(f"[LOG] User {chat_id} chose platform: {user_choice}")
    if user_choice == 'rightmove':
        context.user_data["selected_platform"] = "rightmove"  # Save selected platform
        reply_markup = ReplyKeyboardMarkup(
            [['1', '3'], ['7', '14']],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        send_and_log_message(
            context,
            chat_id,
            "How many days back should we search? (1, 3, 7, or 14)",
            reply_markup=reply_markup
        )
        return CHOOSING_DAYS

    elif user_choice == 'onthemarket':
        context.user_data["selected_platform"] = "onthemarket"  # Save selected platform
        reply_markup = ReplyKeyboardMarkup(
            [['24-hours', '3-days', '7-days']],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        send_and_log_message(
            context,
            chat_id,
            "How recently added should the properties be? (24-hours, 3-days, 7-days)",
            reply_markup=reply_markup
        )
        return CHOOSING_DAYS

    else:
        send_and_log_message(
            context,
            chat_id,
            "Invalid platform. Please choose 'Rightmove' or 'OnTheMarket', or /cancel to exit."
        )
        return CHOOSING_PLATFORM

def choose_days(update: Update, context: CallbackContext) -> int:
    """
    Handle the selection of days for Rightmove or the recently added filter for OnTheMarket.
    """
    chat_id = update.effective_chat.id
    user_choice = update.message.text.strip().lower()

    print(f"[LOG] User {chat_id} chose days/recently added filter: {user_choice}")
    # Check which platform was selected
    selected_platform = context.user_data.get("selected_platform")

    if selected_platform == "rightmove":
        try:
            # Validate days for Rightmove
            days = int(user_choice)
            if days not in [1, 3, 7, 14]:
                raise ValueError

            # Scrape Rightmove properties
            send_and_log_message(context, chat_id, "Scraping Rightmove properties... Please wait.")
            props = scrape_rightmove_properties_with_pagination(days)
            notify_properties(props, chat_id, context)

            # End conversation
            send_and_log_message(
                context,
                chat_id,
                "Done! Type /start for a new search or /cancel to exit."
            )
            return ConversationHandler.END

        except ValueError:
            send_and_log_message(
                context,
                chat_id,
                "Invalid choice. Please select a valid number: 1, 3, 7, or 14. Or /cancel to exit."
            )
            return CHOOSING_DAYS

    elif selected_platform == "onthemarket":
        # Validate recently added filter for OnTheMarket
        valid_choices = ['24-hours', '3-days', '7-days']
        if user_choice in valid_choices:
            send_and_log_message(context, chat_id, f"Scraping OnTheMarket properties added in the last {user_choice}... Please wait.")
            props = scrape_onthemarket_properties(recently_added=user_choice)
            notify_properties(props, chat_id, context)

            # End conversation
            send_and_log_message(
                context,
                chat_id,
                "Done! Type /start for a new search or /cancel to exit."
            )
            return ConversationHandler.END
        else:
            send_and_log_message(
                context,
                chat_id,
                "Invalid choice. Please select '24-hours', '3-days', or '7-days'. Or /cancel to exit."
            )
            return CHOOSING_DAYS

    else:
        # Fallback if platform is not recognized
        send_and_log_message(
            context,
            chat_id,
            "Something went wrong. Please restart with /start or /cancel to exit."
        )
        return ConversationHandler.END


def cancel(update: Update, context: CallbackContext) -> int:
    """
    /cancel command to abort the conversation gracefully.
    """
    chat_id = update.effective_chat.id
    print(f"[LOG] User {chat_id} cancelled the session.")
    send_and_log_message(context, chat_id, "Cancelled. Type /start to begin again.")
    return ConversationHandler.END

# ------------------------------------------------------------------
#   Main Bot Setup
# ------------------------------------------------------------------

def main():
    print("[LOG] Bot is starting...")
    updater = Updater(token=TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_PLATFORM: [
                MessageHandler(Filters.text & ~Filters.command, choose_platform)
            ],
            CHOOSING_DAYS: [
                MessageHandler(Filters.text & ~Filters.command, choose_days)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
        ],
        allow_reentry=True
    )

    dispatcher.add_handler(conv_handler)
    updater.start_polling()
    print("[LOG] Bot is polling for updates.")
    updater.idle()

if __name__ == "__main__":
    main()