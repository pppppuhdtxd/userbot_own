"""
Module metadata for HelpHandler.
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

name = "help_handler"

category = "general"
desc = "راهنما"

help_text = (
    "• `help` | نمایش راهنما\n"
    "• `help <module>` | جزئیات یک ماژول\n"
)

help_extra = (
    "راهنما - اطلاعات تکمیلی\n"
    "\n"
    "دستورات موجود:\n"
    "• `help` | نمایش راهنمای فشرده و دسته‌بندی‌شده\n"
    "• `help <نام ماژول>` | نمایش اطلاعات تکمیلی یک ماژول\n"
    "\n"
    "مثال‌ها:\n"
    "• `help` | لیست همه ماژول‌ها\n"
    "• `help clearer` | جزئیات ماژول clearer\n"
    "• `help join_left` | جزئیات ماژول join_left\n"
    "• `help system` | جزئیات دستورات سیستم\n"
    "\n"
    "نحوه استفاده از ماژول‌ها:\n"
    "• برای دیدن جزئیات هر ماژول، نام آن را بعد از help بنویسید\n"
    "• هر ماژول شامل دستورات، مثال‌ها و نکات مهم است\n"
    "\n"
    "جستجو:\n"
    "• اگر نام دقیق را نمی‌دانید، بخشی از نام را بنویسید\n"
    "• سیستم پیشنهاد‌های مشابه را نمایش می‌دهد\n"
    "\n"
    "دسته‌بندی‌ها:\n"
    "• پاک‌سازی: clearer و auto_clearer\n"
    "• فوروارد: auto_forwarder\n"
    "• اطلاعات: info_handler و whois_handler\n"
    "• عضویت و ترک: join_left\n"
    "• Reaction: reaction_commands\n"
    "• سیستم: system\n"
    "• عمومی: help_handler\n"
    "\n"
    "نکات مهم:\n"
    "• این دستور فقط در Saved Messages کار می‌کند\n"
    "• همه ماژول‌ها برای همه اکانت‌ها قابل دسترسی هستند\n"
)

