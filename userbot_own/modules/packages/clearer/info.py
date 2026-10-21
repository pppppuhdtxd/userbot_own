"""
Module metadata for Clearer.
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

name = "clearer"

category = "cleaning"
desc = "پاک‌سازی دستی پیام‌ها"

help_text = (
    "• `clear` | پاک‌سازی پیش‌فرض (متن و لینک) — فقط پیام‌های خودم در گروه‌ها\n"
    "• `clear all` | پاک‌سازی همه پیام‌های خودم\n"
    "• `clear media` | پاک‌سازی عکس، ویدیو و فایل (فقط پیام‌های خودم در گروه)\n"
    "• `clear pic` | فقط عکس‌ها\n"
    "• `clear vid` | فقط ویدیوها و GIF\n"
    "• `clear file` | فقط فایل‌های ضمیمه\n"
    "• `clear txt` | فقط متن‌های خالص\n"
    "• `clear link` | فقط پیام‌های حاوی لینک\n"
    "• `clear self` | فقط پیام‌های خودم\n"
    "• `clear bot` | فقط پیام‌های ربات‌ها\n"
)

help_extra = (
    "پاک‌سازی دستی پیام‌ها\n"
    "\n"
    "دستورات اصلی:\n"
    "• `clear` | پاک‌سازی پیش‌فرض شامل متن و لینک\n"
    "• `clear all` | پاک‌سازی همه پیام‌های خودم (گروه) یا همه پیام‌ها (چت خصوصی)\n"
    "• `clear media` | پاک‌سازی عکس، ویدیو و فایل بدون لینک\n"
    "\n"
    "فیلتر بر اساس نوع:\n"
    "• `clear pic` | فقط عکس‌ها\n"
    "• `clear vid` | فقط ویدیوها و GIF\n"
    "• `clear file` | فقط فایل‌های ضمیمه\n"
    "• `clear txt` | فقط متن خالص بدون لینک\n"
    "• `clear link` | فقط پیام‌های حاوی لینک یا WebPage\n"
    "\n"
    "فیلتر بر اساس فرستنده:\n"
    "• `clear self` | فقط پیام‌های خودتان\n"
    "• `clear bot` | فقط پیام‌های ربات‌ها\n"
    "\n"
    "رفتار در گروه‌ها و کانال‌ها (v3.0.6):\n"
    "• در گروه، سوپرگروه و کانال: دستور `clear` (و همه حالت‌های آن به‌جز `clear bot`)\n"
    "  فقط پیام‌های خودتان را پیدا و حذف می‌کند. پیام‌های سایر اعضا نادیده گرفته می‌شوند.\n"
    "• در چت خصوصی و مکالمه با ربات: پیام‌های هر دو طرف پردازش می‌شوند.\n"
    "\n"
    "ترکیب دستور و scope:\n"
    "• `clear txt self` | فقط متن‌های خودم\n"
    "• `clear media bot` | فقط رسانه‌های ربات‌ها\n"
    "• `clear all self` | همه پیام‌های خودم\n"
    "\n"
    "ترکیب چند نوع با هم:\n"
    "• `clear txt link` | متن‌ها و لینک‌ها با هم\n"
    "• `clear pic vid` | عکس‌ها و ویدیوها با هم\n"
    "• `clear media link self` | رسانه + لینک، فقط پیام‌های خودم\n"
    "\n"
    "مثال‌ها:\n"
    "• `clear` | حذف متن و لینک‌های خودم\n"
    "• `clear media` | حذف عکس‌ها، ویدیوها و فایل‌های خودم در گروه\n"
    "• `clear self pic` | فقط عکس‌هایی که خودم فرستادم\n"
    "• `clear bot txt` | فقط متن‌های ربات‌ها\n"
    "\n"
    "سیستم طبقه‌بندی:\n"
    "هر پیام فقط یک نوع دارد بر اساس اولویت زیر:\n"
    "`file > vid > pic > link > txt > other`\n"
    "\n"
    "نکات مهم:\n"
    "• `clear media` شامل `link` نمی‌شود\n"
    "• تایپ `link` شامل دکمه‌های شیشه‌ای با لینک هم می‌شود\n"
    "• پیام‌های استیکر، ویس و سایر فقط با `clear all` حذف می‌شوند\n"
    "• 📌 فایل‌های صوتی (mp3, flac, ogg) به عنوان `other` طبقه‌بندی می‌شوند، نه `file`.\n"
    "   برای پاک کردن آن‌ها از `clear all` استفاده کنید.\n"
    "• آرگومان‌های نامعتبر باعث نادیده گرفته شدن کامل دستور می‌شوند\n"
    "• حداکثر ۲۰۰۰ پیام اسکن می‌شود\n"
    "• اگر اسکن دقیقاً به همین محدودیت برسد، در گزارش یادآوری نمایش داده می‌شود\n"
    "• وضعیت private/from_user_filter در لاگ (دیباگ) ثبت می‌شود، نه در پیام چت\n"
    "• اگر برای reaction های مختلف، scope متفاوتی تنظیم کرده‌اید (مثلاً یکی\n"
    "  `clear all` و دیگری `clear self`)، این رفتار عمدی است نه باگ —\n"
    "  فایل `reactions.json` خودتان را بررسی کنید\n"
)

