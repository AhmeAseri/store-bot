import imaplib
import email
import re
import json
import os
import random
import string
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
from playwright.async_api import async_playwright

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAILS_FILE = "mails.json"
URLS_FILE = "urls.json"
CODES_FILE = "codes.json"
ADMIN_ID = 5975882414

IMAP_SERVERS = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "yahoo.com": "imap.mail.yahoo.com",
    "icloud.com": "imap.mail.me.com",
}

def get_imap_server(email_address):
    domain = email_address.split("@")[-1].lower()
    return IMAP_SERVERS.get(domain, f"imap.{domain}")

def load_mails():
    if not os.path.exists(MAILS_FILE):
        return {}
    with open(MAILS_FILE, "r") as f:
        return json.load(f)

def save_mails(mails):
    with open(MAILS_FILE, "w") as f:
        json.dump(mails, f, indent=2)

def load_urls():
    if not os.path.exists(URLS_FILE):
        return {}
    with open(URLS_FILE, "r") as f:
        return json.load(f)

def save_urls(urls):
    with open(URLS_FILE, "w") as f:
        json.dump(urls, f, indent=2)

def load_codes():
    if not os.path.exists(CODES_FILE):
        return {}
    with open(CODES_FILE, "r") as f:
        return json.load(f)

def save_codes(codes):
    with open(CODES_FILE, "w") as f:
        json.dump(codes, f, indent=2)

def generate_codes(service, count=20):
    codes = load_codes()
    new_codes = []
    for _ in range(count):
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
        codes[code] = {
            "service": service,
            "uses_left": 3,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        new_codes.append(code)
    save_codes(codes)
    return new_codes

def check_code(code):
    codes = load_codes()
    if code not in codes:
        return None, "invalid"
    entry = codes[code]
    if entry["uses_left"] <= 0:
        return None, "expired"
    return entry["service"], "valid"

def use_code(code):
    codes = load_codes()
    if code in codes:
        codes[code]["uses_left"] -= 1
        save_codes(codes)

def is_admin(user_id):
    return user_id == ADMIN_ID

def fetch_otp_email(email_user, email_pass, service_keyword):
    try:
        imap_server = get_imap_server(email_user)
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(email_user, email_pass)
        mail.select("inbox")
        
        since_time = (datetime.now() - timedelta(minutes=10)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{since_time}")')
        email_ids = messages[0].split()
        email_ids.reverse()
        
        for eid in email_ids[:30]:
            _, msg_data = mail.fetch(eid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subject = str(msg.get("subject", "")).lower()
            sender = str(msg.get("from", "")).lower()
            
            if service_keyword.lower() not in subject and service_keyword.lower() not in sender:
                continue
            
            body = ""
            html_body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors="ignore")
                    elif part.get_content_type() == "text/html":
                        html_body = part.get_payload(decode=True).decode(errors="ignore")
            else:
                body = msg.get_payload(decode=True).decode(errors="ignore")
            
            full_text = body if body else html_body
            
            otp_patterns = [
                r'(?:verification|verify|confirm|otp|code|رمز|كود|تحقق)[\s:]*?(\d{4,8})',
                r'(\d{4,8})[\s]*?(?:is your|verification|verify|code|رمز)',
                r'^\s*(\d{4,6})\s*$',
                r'(\d{4})-(\d{4})',
            ]
            
            for pattern in otp_patterns:
                matches = re.finditer(pattern, full_text, re.IGNORECASE | re.MULTILINE)
                for match in matches:
                    if len(match.groups()) > 1:
                        code = match.group(1) + match.group(2)
                    else:
                        code = match.group(1)
                    
                    if 4 <= len(code) <= 8 and code.isdigit():
                        mail.logout()
                        return {"type": "otp", "value": code}
            
            link_patterns = [
                r'https?://[^\s"<>\']+/(?:login|signin|auth|verify)[^\s"<>\']*',
                r'https?://[^\s"<>\']+\?[^\s"<>\']*(?:code|token|verify)[^\s"<>\']*',
            ]
            
            for pattern in link_patterns:
                link_match = re.search(pattern, full_text, re.IGNORECASE)
                if link_match:
                    mail.logout()
                    return {"type": "link", "value": link_match.group(0)}
        
        mail.logout()
        return None
    except Exception as e:
        return {"type": "error", "value": str(e)}

async def fetch_otp_url(url):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=30000)
            await page.wait_for_timeout(8000)

            try:
                sign_in_section = await page.locator("text=Sign In Code").locator("..").inner_text()
                otp_match = re.search(r'\b(\d{4,8})\b', sign_in_section)
                if otp_match:
                    code = otp_match.group(1)
                    if 4 <= len(code) <= 8:
                        await browser.close()
                        return {"type": "otp", "value": code}
            except:
                pass

            try:
                all_text = await page.inner_text("body")
                matches = re.finditer(r'^\s*(\d{4,8})\s*$', all_text, re.MULTILINE)
                for match in matches:
                    code = match.group(1)
                    if 4 <= len(code) <= 8 and code.isdigit():
                        await browser.close()
                        return {"type": "otp", "value": code}
            except:
                pass

            try:
                temp_link_section = await page.locator("text=Temporary Link").locator("..").inner_text()
                link_match = re.search(r'https?://[^\s"<>\']+', temp_link_section)
                if link_match:
                    await browser.close()
                    return {"type": "link", "value": link_match.group(0)}
            except:
                pass

            try:
                all_links = await page.locator("a").all()
                for link in all_links[:5]:
                    href = await link.get_attribute("href")
                    if href and href.startswith("http"):
                        await browser.close()
                        return {"type": "link", "value": href}
            except:
                pass

            await browser.close()
            return None
    except Exception as e:
        return {"type": "error", "value": str(e)}

# ======= واجهة العميل (محسّنة) =======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_admin(user_id):
        await show_admin_menu(update)
        return
    
    await update.message.reply_text(
        "🎯 *أهلاً بك في بوت الأكواد!*\n\n"
        "لتبدأ، أرسل كود الاشتراك الخاص بك:",
        parse_mode="Markdown"
    )
    context.user_data["waiting_code"] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_admin(user_id):
        return
    if not context.user_data.get("waiting_code"):
        await update.message.reply_text("⚠️ اضغط /start للبدء")
        return
    
    code = update.message.text.strip().upper()
    service, status = check_code(code)
    
    if status == "invalid":
        await update.message.reply_text("❌ الكود غير صحيح\nتحقق من الكود وحاول مرة أخرى")
        return
    if status == "expired":
        await update.message.reply_text("⏰ انتهت استخدامات هذا الكود\nتواصل مع الدعم")
        return
    
    context.user_data["waiting_code"] = False
    context.user_data["service"] = service
    context.user_data["code"] = code
    
    codes = load_codes()
    uses_left = codes[code]["uses_left"]
    
    keyboard = [[InlineKeyboardButton(f"📲 احصل على كود {service.upper()}", callback_data=f"get_{service}")]]
    await update.message.reply_text(
        f"✅ *تم التحقق بنجاح!*\n\n"
        f"📦 خدمتك: `{service.upper()}`\n"
        f"🔄 استخدامات متبقية: `{uses_left}/3`\n\n"
        f"اضغط الزر أدناه للحصول على الكود:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "admin_menu":
        if not is_admin(user_id): return
        keyboard = [
            [InlineKeyboardButton("➕ إضافة إيميل", callback_data="admin_add")],
            [InlineKeyboardButton("🔗 إضافة رابط", callback_data="admin_add_url")],
            [InlineKeyboardButton("📋 عرض الحسابات", callback_data="admin_list")],
            [InlineKeyboardButton("🗑️ حذف حساب", callback_data="admin_delete")],
            [InlineKeyboardButton("🎟️ عرض الأكواد", callback_data="admin_codes")],
        ]
        await query.edit_message_text(
            "*⚙️ لوحة تحكم الأدمن*\n\nاختر الإجراء:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    if data == "admin_add":
        if not is_admin(user_id): return
        await query.edit_message_text(
            "*➕ إضافة إيميل جديد*\n\n"
            "أرسل الأمر بهذا الشكل:\n\n"
            "`/addmail اسم_الخدمة الإيميل الباسورد`\n\n"
            "📌 مثال:\n"
            "`/addmail netflix store@gmail.com abc123xyz`",
            parse_mode="Markdown"
        )
        return

    if data == "admin_add_url":
        if not is_admin(user_id): return
        await query.edit_message_text(
            "*🔗 إضافة رابط جديد*\n\n"
            "أرسل الأمر بهذا الشكل:\n\n"
            "`/addurl اسم_الخدمة الرابط`\n\n"
            "📌 مثال:\n"
            "`/addurl netflix https://code.tvleb.com/xxxxx`",
            parse_mode="Markdown"
        )
        return

    if data == "admin_list":
        if not is_admin(user_id): return
        mails = load_mails()
        urls = load_urls()
        text = "*📋 الحسابات المضافة:*\n\n"
        if mails:
            text += "*📧 الإيميلات:*\n"
            for s, d in mails.items():
                text += f"• `{s}` → {d['email']}\n"
        if urls:
            text += "\n*🔗 الروابط:*\n"
            for s in urls.keys():
                text += f"• `{s}`\n"
        if not mails and not urls:
            text = "📭 لا توجد حسابات مضافة"
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_menu")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "admin_codes":
        if not is_admin(user_id): return
        mails = load_mails()
        urls = load_urls()
        all_services = list(set(list(mails.keys()) + list(urls.keys())))
        if not all_services:
            await query.edit_message_text("📭 لا توجد خدمات")
            return
        keyboard = [[InlineKeyboardButton(f"🎟️ {s.upper()}", callback_data=f"codes_{s}")] for s in all_services]
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_menu")])
        await query.edit_message_text("*🎟️ اختر الخدمة:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("codes_"):
        if not is_admin(user_id): return
        service = data.replace("codes_", "")
        codes = load_codes()
        service_codes = {k: v for k, v in codes.items() if v["service"] == service}
        if not service_codes:
            await query.edit_message_text(f"📭 لا توجد أكواد لـ {service}")
            return
        text = f"*🎟️ أكواد {service.upper()}:*\n\n"
        for code, info in service_codes.items():
            status = "🔥" if info['uses_left'] == 1 else "✅"
            text += f"{status} `{code}` → {info['uses_left']}/3\n"
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_codes")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "admin_delete":
        if not is_admin(user_id): return
        mails = load_mails()
        urls = load_urls()
        all_services = list(set(list(mails.keys()) + list(urls.keys())))
        if not all_services:
            await query.edit_message_text("📭 لا توجد حسابات")
            return
        keyboard = [[InlineKeyboardButton(f"🗑️ {s}", callback_data=f"del_{s}")] for s in all_services]
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_menu")])
        await query.edit_message_text("*اختر الحساب للحذف:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("del_"):
        if not is_admin(user_id): return
        service = data.replace("del_", "")
        mails = load_mails()
        urls = load_urls()
        if service in mails:
            del mails[service]
            save_mails(mails)
        if service in urls:
            del urls[service]
            save_urls(urls)
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_menu")]]
        await query.edit_message_text(f"✅ تم حذف `{service}` بنجاح", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # العميل يطلب كود
    if data.startswith("get_"):
        service = data.replace("get_", "")
        user_service = context.user_data.get("service")
        user_code = context.user_data.get("code")

        if user_service != service:
            await query.edit_message_text("❌ ليس لديك صلاحية لهذه الخدمة")
            return

        await query.edit_message_text("⏳ جاري جلب الكود...")

        mails = load_mails()
        urls = load_urls()
        result = None

        if service in urls:
            result = await fetch_otp_url(urls[service]["url"])
        elif service in mails:
            result = fetch_otp_email(mails[service]["email"], mails[service]["password"], service)

        use_code(user_code)
        codes = load_codes()
        uses_left = codes.get(user_code, {}).get("uses_left", 0)

        retry_btn = []
        if uses_left > 0:
            retry_btn = [[InlineKeyboardButton(f"🔄 كود جديد ({uses_left} متبقي)", callback_data=f"get_{service}")]]

        if not result:
            await query.edit_message_text(
                "⚠️ لم يتم العثور على الكود\n\n"
                "تأكد من طلب الكود من التطبيق أولاً",
                reply_markup=InlineKeyboardMarkup(retry_btn) if retry_btn else None
            )
        elif result["type"] == "error":
            await query.edit_message_text("❌ خطأ في الاتصال - تواصل مع الدعم")
        elif result["type"] == "otp":
            await query.edit_message_text(
                f"✅ *كود {service.upper()}*\n\n"
                f"🔑 `{result['value']}`\n\n"
                f"⏱️ صالح لدقائق قليلة",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(retry_btn) if retry_btn else None
            )
        elif result["type"] == "link":
            keyboard = [[InlineKeyboardButton("🔗 فتح رابط الدخول", url=result['value'])]]
            if retry_btn:
                keyboard.append(retry_btn[0])
            await query.edit_message_text(
                f"✅ *رابط دخول {service.upper()} جاهز*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

async def show_admin_menu(update):
    keyboard = [
        [InlineKeyboardButton("➕ إضافة إيميل", callback_data="admin_add")],
        [InlineKeyboardButton("🔗 إضافة رابط", callback_data="admin_add_url")],
        [InlineKeyboardButton("📋 عرض الحسابات", callback_data="admin_list")],
        [InlineKeyboardButton("🗑️ حذف حساب", callback_data="admin_delete")],
        [InlineKeyboardButton("🎟️ عرض الأكواد", callback_data="admin_codes")],
    ]
    await update.message.reply_text(
        "*⚙️ لوحة تحكم الأدمن*\n\n"
        "اختر الإجراء المطلوب:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def add_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ للأدمن فقط")
        return
    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ صيغة خاطئة\n\n"
            "الصيغة: `/addmail الخدمة الإيميل الباسورد`",
            parse_mode="Markdown"
        )
        return
    service = context.args[0].lower()
    mails = load_mails()
    mails[service] = {"email": context.args[1], "password": context.args[2]}
    save_mails(mails)
    new_codes = generate_codes(service, 20)
    codes_text = "\n".join([f"`{c}`" for c in new_codes])
    await update.message.reply_text(
        f"✅ *تم إضافة {service} بنجاح!*\n\n"
        f"🎟️ *الأكواد الجديدة:* _(كل كود يستخدم 3 مرات)_\n\n{codes_text}",
        parse_mode="Markdown"
    )

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ للأدمن فقط")
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ صيغة خاطئة\n\n"
            "الصيغة: `/addurl الخدمة الرابط`",
            parse_mode="Markdown"
        )
        return
    service = context.args[0].lower()
    url = context.args[1]
    urls = load_urls()
    urls[service] = {"url": url}
    save_urls(urls)
    new_codes = generate_codes(service, 20)
    codes_text = "\n".join([f"`{c}`" for c in new_codes])
    await update.message.reply_text(
        f"✅ *تم إضافة رابط {service} بنجاح!*\n\n"
        f"🎟️ *الأكواد الجديدة:* _(كل كود يستخدم 3 مرات)_\n\n{codes_text}",
        parse_mode="Markdown"
    )

async def delete_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("❌ اكتب اسم الخدمة")
        return
    service = context.args[0].lower()
    mails = load_mails()
    urls = load_urls()
    if service in mails:
        del mails[service]
        save_mails(mails)
    if service in urls:
        del urls[service]
        save_urls(urls)
    await update.message.reply_text(f"🗑️ تم حذف `{service}` بنجاح", parse_mode="Markdown")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addmail", add_mail))
    app.add_handler(CommandHandler("addurl", add_url))
    app.add_handler(CommandHandler("deletemail", delete_mail))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ البوت شغّال...")
    app.run_polling()

if __name__ == "__main__":
    main()