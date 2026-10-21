"""
Module metadata for SystemModule.
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

name = "system"

category = "system"
desc = "مدیریت سیستم"

help_text = (
    "• `.modules` | لیست ماژول‌های فعال\n"
    "• `.account` | اطلاعات اکانت فعلی\n"
    "• `.stats`   | آمار کلی سیستم\n"
    "• `.ping`    | تست تأخیر اتصال\n"
)

help_extra = (
    "دستورات سیستم (فقط خواندنی)\n"
    "\n"
    "همه این دستورات فقط در Saved Messages کار می‌کنند.\n"
    "\n"
    "وضعیت سیستم:\n"
    "• `.modules` | نمایش لیست همه ماژول‌های فعال این اکانت\n"
    "• `.stats`   | آمار کلی شامل اکانت‌ها، ماژول‌ها و uptime\n"
    "• `.ping`    | تست سرعت پاسخگویی و تأخیر اتصال به سرورهای تلگرام\n"
    "\n"
    "مدیریت اکانت‌ها:\n"
    "• `.account` | اطلاعات کامل اکانت فعلی\n"
    "\n"
    "مثال‌ها:\n"
    "• `.modules` | نمایش ۹ ماژول فعال\n"
    "• `.ping`    | بررسی تأخیر اتصال (API و Edit Latency)\n"
    "• `.account` | نمایش User ID, API ID, Connection\n"
    "• `.stats`   | نمایش uptime و تعداد اکانت‌های متصل\n"
    "\n"
    "نکات مهم:\n"
    "• اتصال به‌صورت مستقیم انجام می‌شود\n"
    "• برای دور زدن محدودیت‌های شبکه از VPN سیستمی استفاده کنید\n"
    "• خروجی همه دستورات پس از ۸ ثانیه به‌صورت خودکار حذف می‌شود\n"
)

