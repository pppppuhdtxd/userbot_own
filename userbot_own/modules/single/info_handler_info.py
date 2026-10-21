"""
Module metadata for InfoHandler.
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

name = "info_handler"

category = "info"
desc = "اطلاعات پیام"

help_text = (
    "• `info` (reply) | نمایش اطلاعات کامل پیام reply شده\n"
)

help_extra = (
    "اطلاعات پیام - نمایش اطلاعات جامع پیام reply شده\n"
    "\n"
    "دستور اصلی:\n"
    "• `info` (reply) | نمایش اطلاعات کامل پیام reply شده\n"
    "\n"
    "اطلاعات نمایش داده‌شده:\n"
    "• شناسه‌ها | Message ID, Chat ID, Sender ID\n"
    "• زمان | زمان ارسال و ویرایش\n"
    "• آمار | بازدید و فوروارد (برای کانال‌ها)\n"
    "• محتوا | نوع پیام بر اساس سیستم طبقه‌بندی\n"
    "• Entities | Bold, Italic, Code, URL, Mention و غیره\n"
    "• فرستنده | نام، یوزرنیم، ID، وضعیت (Bot/Verified/Premium)، نوع Channel/Supergroup/Group برای فرستنده‌های ناشناس\n"
    "• چت | نوع چت، عنوان، لینک عمومی، تعداد اعضا\n"
    "• Reply | اطلاعات پیام reply شده\n"
    "\n"
    "جزئیات اختصاصی هر نوع:\n"
    "• فایل | File ID, حجم، فرمت، MIME type\n"
    "• ویدیو | Video ID, مدت، ابعاد، Streaming support, نوع (دایره‌ای/معمولی)\n"
    "• عکس | Photo ID, ابعاد بزرگ‌ترین سایز، حجم تقریبی\n"
    "  ↳ عکس ارسال‌شده به صورت فایل سند هم پشتیبانی می‌شود (File ID, حجم، ابعاد)\n"
    "• لینک | WebPage Preview (URL, Site, Title, Description)\n"
    "  ↳ URL entities در متن\n"
    "  ↳ دکمه‌های شیشه‌ای (inline keyboard URL buttons)\n"
    "• سایر | Contact, Location, Poll, Sticker, Voice, Audio File\n"
    "\n"
    "ویژگی‌های پیام:\n"
    "• ویرایش‌شده، فوروارد شده، سنجاق شده\n"
    "• بی‌صدا، منشن شده، خروجی\n"
    "• بدون فوروارد، زمان‌بندی شده\n"
    "\n"
    "مثال‌ها:\n"
    "• reply روی یک عکس + `info` | Photo ID، ابعاد، حجم\n"
    "• reply روی یک ویدیو + `info` | مدت، ابعاد، MIME\n"
    "• reply روی پیام متنی با لینک + `info` | WebPage preview\n"
    "• reply روی پیام با دکمه شیشه‌ای + `info` | دکمه‌ها و URLهای آن‌ها\n"
    "• reply روی فایل صوتی + `info` | Artist, Title, Duration\n"
    "\n"
    "نکات مهم:\n"
    "• این دستور در هر چتی قابل استفاده است\n"
    "• حتماً باید به یک پیام reply شود\n"
    "• فایل‌های صوتی (.mp3, .flac) به عنوان 'سایر' طبقه‌بندی می‌شوند\n"
    "• اطلاعات کامل WebPage برای لینک‌های deep link ربات‌ها هم نمایش داده می‌شود\n"
)

