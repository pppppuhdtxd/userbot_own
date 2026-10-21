"""
Module metadata for AutoClearer.
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

name = "auto_clearer"

category = "cleaning"
desc = "پاک‌سازی خودکار"

help_text = (
    "• `autoclear <type> on <scope>` | فعال‌سازی پاک‌سازی خودکار\n"
    "• `autoclear <type> off <scope>` | غیرفعال‌سازی پاک‌سازی خودکار\n"
    "• `autoclear status` | نمایش وضعیت فعلی\n"
)

help_extra = (
    "پاک‌سازی خودکار پیام‌های ربات\n"
    "\n"
    "دستور اصلی:\n"
    "• `autoclear <type> on <scope>` | فعال‌سازی پاک‌سازی خودکار\n"
    "• `autoclear <type> off <scope>` | غیرفعال‌سازی پاک‌سازی خودکار\n"
    "• `autoclear status` | نمایش وضعیت فعلی\n"
    "\n"
    "انواع پیام (type):\n"
    "• `file` | فایل‌های ضمیمه (PDF, ZIP, و غیره)\n"
    "• `vid` | ویدیوها و GIF\n"
    "• `pic` | عکس‌ها\n"
    "• `link` | پیام‌های حاوی لینک یا WebPage\n"
    "• `txt` | متن خالص بدون لینک\n"
    "• `media` | عکس، ویدیو و فایل بدون لینک\n"
    "• `other` | سایر: استیکر، نظرسنجی، مخاطب، موقعیت مکانی، ویس و سایر\n"
    "\n"
    "📌 فایل‌های صوتی (mp3, flac, ogg) به عنوان `other` طبقه‌بندی می‌شوند، نه `file`.\n"
    "   برای پاک کردن خودکار آن‌ها از `autoclear other on 1` استفاده کنید.\n"
    "\n"
    "محدوده (scope):\n"
    "• `1` | فقط پیام‌های ربات\n"
    "• `2` | فقط پیام‌های شما\n"
    "• `3` | هر دو\n"
    "\n"
    "مکان استفاده:\n"
    "• در Saved Messages | تنظیم کلی برای همه ربات‌ها\n"
    "• در چت یک ربات | تنظیم مخصوص همان ربات\n"
    "\n"
    "مثال‌ها:\n"
    "• `autoclear txt on 3` | پاک‌سازی خودکار همه متن‌ها\n"
    "• `autoclear pic on 1` | پاک‌سازی فقط عکس‌های ربات\n"
    "• `autoclear link on 3` | پاک‌سازی خودکار لینک‌ها\n"
    "• `autoclear media off 3` | غیرفعال کردن رسانه‌ها\n"
    "• `autoclear other on 1` | پاک‌سازی خودکار استیکر و ویس ربات\n"
    "• `autoclear status` | نمایش وضعیت فعلی\n"
    "\n"
    "سیستم طبقه‌بندی:\n"
    "هر پیام فقط یک نوع دارد بر اساس اولویت زیر:\n"
    "`file > vid > pic > link > txt > other`\n"
    "\n"
    "نکات مهم:\n"
    "• `media` شامل `link` نمی‌شود\n"
    "• `link` شامل دکمه‌های شیشه‌ای با لینک هم می‌شود\n"
    "• `other` شامل استیکر، ویس، نظرسنجی، مخاطب، موقعیت مکانی و صدا هم می‌شود\n"
    "• `other` پیش‌فرض خاموش است تا از حذف تصادفی استیکر/ویس جلوگیری شود\n"
    "• تنظیمات در `autoclear.json` ذخیره می‌شوند\n"
    "• پیام‌های پین شده هرگز پاک نمی‌شوند\n"
    "• هنگام فعال‌سازی، پیام‌های قدیمی‌ِ آن نوع هم پاک می‌شوند (با رعایت scope)\n"
)

