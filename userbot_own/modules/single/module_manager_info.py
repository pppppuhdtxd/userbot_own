"""
Module metadata for ModuleManager.
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

name = "module_manager"

category = "system"
desc = "مدیریت ماژول‌های اضافی"

help_text = (
    "• `.extra` | لیست ماژول‌های اضافی\n"
    "• `.extra enable <name>` | فعال‌سازی یک ماژول\n"
    "• `.extra disable <name>` | غیرفعال‌سازی یک ماژول\n"
    "• `.extra info <name>` | جزئیات یک ماژول\n"
)

help_extra = (
    "📦 **مدیریت ماژول‌های اضافی (`modules_extra/`)**\n"
    "\n"
    "ماژول‌های داخل `modules_extra/`، برخلاف ماژول‌های اصلی `modules/`، به‌صورت پیش‌فرض غیرفعال هستند و باید برای هر اکانت جداگانه فعال شوند.\n"
    "\n"
    "**دستورات:**\n"
    "• `.extra` | نمایش همه ماژول‌های اضافه با وضعیت ✅ فعال / ❌ غیرفعال\n"
    "• `.extra enable <name>` | فعال‌سازی — بلافاصله لود و اجرا می‌شود\n"
    "• `.extra disable <name>` | غیرفعال‌سازی — بلافاصله متوقف می‌شود\n"
    "• `.extra info <name>` | نمایش وضعیت، دسته‌بندی و توضیح ماژول\n"
    "\n"
    "**مثال‌ها:**\n"
    "• `.extra enable example_module` | فعال‌سازی ماژول نمونه\n"
    "• `help example_module` | پس از فعال‌سازی، در راهنما هم نمایش داده می‌شود\n"
    "\n"
    "**نکات مهم:**\n"
    "• ماژول‌های غیرفعال هرگز import نمی‌شوند — صفر overhead\n"
    "• فعال/غیرفعال بودن هر ماژول per-account است، نه global\n"
    "• ویرایش مستقیم `enabled_modules.json` هم پشتیبانی می‌شود و به‌صورت خودکار شناسایی می‌شود — نتیجه با استفاده از دستورات بالا یکسان است\n"
    "• این دستور فقط در Saved Messages کار می‌کند\n"
)

