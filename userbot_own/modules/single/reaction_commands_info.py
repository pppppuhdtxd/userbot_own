"""
Module metadata for ReactionCommands.
============================================================

DATA ONLY — this file must not import anything from the project.
It is read by the module's __init__.py (which copies the fields
onto the Module subclass) and by help_handler.py. Keeping it
import-free makes an import cycle structurally impossible.

Fields
------
name       Logical module name; must match the module's file/folder name.
category   One of: cleaning, forward, info, social, reaction, system, general.
desc       One-line Persian description shown next to the name in `help`.
help_text  Compact help shown in the `help` listing.
help_extra Detailed help shown via `help <module>`.
"""

name = "reaction_commands"

category = "reaction"
desc = "دستورات با ری‌اکشن"

help_text = (
    "• `reactions` | لیست reaction های تنظیم‌شده\n"
    "• `reaction add <emoji> <command>` | افزودن mapping\n"
    "• `reaction remove <emoji>` | حذف یک mapping\n"
    "• `reaction clear` | حذف همه mapping ها\n"
    "• `.reaction_scope` | نمایش/تغییر محدوده فعال (چت خصوصی/ربات/گروه/کانال)\n"
)

help_extra = (
    "Reaction Commands - اجرای دستورات با ری‌اکشن\n"
    "\n"
    "دستورات اصلی:\n"
    "• `reactions` | نمایش لیست همه reaction های فعال\n"
    "• `reaction add <emoji> <command>` | افزودن mapping جدید\n"
    "• `reaction remove <emoji>` | حذف یک mapping\n"
    "• `reaction clear` | حذف همه mapping ها\n"
    "\n"
    "محدوده فعال (Scope) — از نسخه ۳.۰.۸:\n"
    "دیگر نیازی به ویرایش فایل ماژول نیست؛ محدوده چت‌هایی که ری‌اکشن در آن‌ها\n"
    "پردازش می‌شود کاملاً از طریق دستور زیر و در زمان اجرا قابل تنظیم است:\n"
    "• `.reaction_scope` | نمایش وضعیت فعلی محدوده\n"
    "• `.reaction_scope private` | فعال/غیرفعال‌سازی چت خصوصی با کاربران\n"
    "• `.reaction_scope bot` | فعال/غیرفعال‌سازی چت خصوصی با ربات‌ها\n"
    "• `.reaction_scope group` | فعال/غیرفعال‌سازی گروه‌ها **و سوپرگروه‌ها**\n"
    "• `.reaction_scope channel` | فعال/غیرفعال‌سازی کانال‌های broadcast\n"
    "• `.reaction_scope all` | فعال‌سازی همه محدوده‌ها\n"
    "• `.reaction_scope none` | غیرفعال‌سازی همه محدوده‌ها\n"
    "هر بار اجرای دستور با یک مقدار، همان مقدار را toggle می‌کند (روشن↔خاموش).\n"
    "پیش‌فرض کارخانه‌ای: فقط `bot` فعال است. تنظیمات در `reaction_scope.json`\n"
    "(به‌ازای هر اکانت، جدا از `reactions.json`) ذخیره می‌شود.\n"
    "\n"
    "روش‌های تشخیص ری‌اکشن:\n"
    "این ماژول از ۲ روش push-based استفاده می‌کند (بدون polling):\n"
    "• Method 1 | `UpdateMessageReactions` برای پیام‌های خودتان\n"
    "• Method 2 | `UpdateEditMessage` که گاهی تلگرام این را می‌فرستد\n"
    "\n"
    "اجرای مستقیم ماژول‌ها:\n"
    "دستورات به‌جای `send_message` مستقیماً اجرا می‌شوند:\n"
    "• `clear` | اجرای مستقیم `clearer.py`\n"
    "• `join` / `left` | اجرای مستقیم `join_left.py`\n"
    "• `info` | اجرای مستقیم `info_handler.py`\n"
    "• `whois` | اجرای مستقیم `whois_handler.py`\n"
    "\n"
    "مثال‌ها:\n"
    "• `reaction add 👌 clear txt` | پاک کردن متن‌ها با ری‌اکشن 👌\n"
    "• `reaction add 👍 join` | عضویت با ری‌اکشن 👍\n"
    "• `reaction add 🔍 info` | نمایش اطلاعات پیام با 🔍\n"
    "• `reaction add 👤 whois` | نمایش اطلاعات فرستنده با 👤\n"
    "• `reaction remove 👌` | حذف mapping 👌\n"
    "• `reaction clear` | حذف همه mapping ها\n"
    "• `.reaction_scope group` | فعال‌سازی ری‌اکشن در گروه‌ها/سوپرگروه‌ها\n"
    "\n"
    "نکات مهم:\n"
    "• فقط ری‌اکشن‌های خودتان (self-reaction) تشخیص داده می‌شوند\n"
    "• تنظیمات mapping در `reactions.json` و scope در `reaction_scope.json` ذخیره می‌شوند\n"
    "• Loop prevention از اجرای تکراری جلوگیری می‌کند — حتی پس از قطعی و اتصال مجدد شبکه\n"
    "• اگر اجرای مستقیم ممکن نباشد، به `send_message` fallback می‌شود\n"
    "• ⚠️ اگر ری‌اکشن‌های مختلف را به scope متفاوتی مپ کرده‌اید (مثلاً 👌 به\n"
    "  `clear all` و 👍 به `clear self`)، رفتار متفاوت هر ری‌اکشن عمدی است\n"
    "  نه باگ — `reactions.json` خودتان را بررسی کنید\n"
    "• لاگ دیباگ هر ری‌اکشن شامل chat_kind هم می‌شود تا در صورت بروز مشکل،\n"
    "  علت (طبقه‌بندی چت یا تنظیم scope) بلافاصله مشخص باشد\n"
)

