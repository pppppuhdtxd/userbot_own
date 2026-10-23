"""
Module metadata for BotLinkStarter.
============================================================

DATA ONLY — this file must not import anything from the project.
It is read by the module's __init__.py (which copies the fields onto
the Module subclass) and by help_handler.py. Keeping it import-free
makes an import cycle structurally impossible.

Fields
------
name       Logical module name; must match the module's file/folder name.
category   One of: cleaning, forward, info, social, reaction, system, general.
desc       One-line Persian description shown next to the name in `help`.
help_text  Compact help shown in the `help` listing.
help_extra Detailed help shown via `help <module>`.
"""

name = "bot_link_starter"

category = "general"
desc = "استارت خودکار لینک‌های بات"

help_text = (
    "• `start` (Quote & Reply) | استارت خودکار لینک‌های باتِ بخش Quote‌شده\n"
)

help_extra = (
    "استارت خودکار لینک‌های بات\n"
    "\n"
    "این ماژول لینک‌های موجود در بخش Quote & Reply یک پیام را به ترتیب\n"
    "پیدا می‌کند و برای هرکدام دستور `/start` را به همان بات می‌فرستد تا\n"
    "فایل یا مدیایی که آن لینک ارائه می‌دهد تحویل گرفته شود.\n"
    "\n"
    "نحوهٔ کار:\n"
    "• در Saved Messages، بخشی از پیام حاوی لینک‌ها را Quote & Reply کنید\n"
    "• سپس پیام `start` را بفرستید\n"
    "• ماژول فقط لینک‌های داخل بخش Quote‌شده را استارت می‌کند — نه کل پیام\n"
    "• اگر کل پیام را Quote کنید (بدون انتخاب بخشی از متن)، کل پیام Reply‌شده\n"
    "  بررسی می‌شود\n"
    "• لینک‌ها به ترتیبِ ظاهرشان در متن، یکی‌یکی و با فاصله استارت می‌شوند\n"
    "\n"
    "انواع لینک شناسایی‌شده:\n"
    "• لینک معمولی: `https://t.me/MyBot?start=ref123`\n"
    "• لینک پشت متن (Create Link): متنی که با فرمت create link به لینک\n"
    "  وصل شده باشد — همان لینکی که با کلیک روی آن وارد بات می‌شوید\n"
    "• لینک بدون پروتکل: `t.me/MyBot?start=ref123`\n"
    "\n"
    "اگر دستور را در Saved Messages بفرستید ولی پیام Reply‌شده در کانال\n"
    "باشد (مثلاً پستی که از کانال فوروارد شده)، ماژول پیام را از همان\n"
    "کانال می‌خواند — نه فقط از Saved Messages.\n"
    "\n"
    "مهم — دربارهٔ لینک پشت متن:\n"
    "هدفِ یک لینک Create Link فقط داخل Entity ذخیره می‌شود، نه در متن.\n"
    "در پیام‌هایی مثل فهرست چپترها، متن پیام اصلاً آدرس ندارد و تمام\n"
    "لینک‌ها فقط Entity هستند.\n"
    "تلگرام Entityهای Quote را ذخیره نمی‌کند (تست‌شده روی API واقعی:\n"
    "quote_entities همیشه None برمی‌گردد)، پس خواندن خودِ Quote کافی نیست.\n"
    "به همین دلیل ماژول Entityها را از پیام اصلی می‌خواند و آن‌ها را با\n"
    "Quote تطبیق می‌دهد — دقیقاً همان کاری که دستور `info` روی پیام اصلی\n"
    "انجام می‌دهد. تطبیق در سه مرحله انجام می‌شود:\n"
    "۱. پیدا کردن متن Quote به‌صورت عیناً داخل پیام\n"
    "۲. همان جستجو با نادیده‌گرفتن فاصله‌ها و کاراکترهای مخفی\n"
    "۳. تطبیق بر اساس متنِ دیده‌شدهٔ هر Entity (مثلاً «Chapter 557»)\n"
    "نتیجه: Create Link درست شناسایی می‌شود، بدون اینکه بقیهٔ پیام استارت شود.\n"
    "\n"
    "مثال:\n"
    "• پیامی با ۳ لینک را Quote & Reply کنید و `start` بفرستید\n"
    "• ماژول `/start ref123` را به هر سه بات، به ترتیب، می‌فرستد\n"
    "\n"
    "نکات مهم:\n"
    "• فقط لینک‌های بات‌های تلگرام (t.me) پردازش می‌شوند\n"
    "• لینک‌های دعوت‌نامه (`joinchat`، `+hash`) و کانال خصوصی (`c/...`)\n"
    "  نادیده گرفته می‌شوند — `/start` برای آن‌ها معنا ندارد\n"
    "• هر لینک جداگانه استارت می‌شود، حتی اگر چند لینک به یک بات باشند؛\n"
    "  مقدار `?start=` تعیین‌کنندهٔ فایل یا آیتم درخواستی است\n"
    "• گزارش نتیجه روی همان پیام دستور `start` نوشته می‌شود\n"
    "• بین دو استارت ۳ ثانیه فاصله گذاشته می‌شود تا Telegram محدودیت\n"
    "  نرخ ارسال را اعمال نکند\n"
    "• اگر بات پاسخ ندهد، جداگانه گزارش می‌شود و بقیهٔ لینک‌ها ادامه می‌یابند\n"
    "• برای غیرفعال‌کردن: `.extra disable bot_link_starter`\n"
)
