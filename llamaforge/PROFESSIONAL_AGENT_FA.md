# Agent حرفه‌ای — LlamaForge 0.24

این نسخه چهار تغییر اصلی دارد:

1. **Auto Route قبل از Skillها**
   - پیام‌های عادی مثل سلام، توضیح مفهومی، نوشتن متن یا سؤال دانشی مستقیم به مدل می‌روند.
   - فقط درخواست‌هایی که واقعاً به وب، URL، اطلاعات جاری، API، دانلود یا تعامل مرورگر نیاز دارند وارد Agent/Skill Loop می‌شوند.
   - صرف وجود کلماتی مثل API، HTTP یا Website به معنی اجرای Skill نیست؛ باید درخواست عملیاتی باشد.

2. **زبان داخلی جدا از زبان پاسخ**
   - Promptهای برنامه‌ریزی Agent عمداً انگلیسی و ساختاریافته باقی مانده‌اند تا مدل‌های لوکال کوچک‌تر پایدارتر تصمیم بگیرند.
   - زبان آخرین پیام کاربر تشخیص داده می‌شود.
   - اگر Agent در پایان پاسخ را به زبان اشتباه تولید کند، یک Finalizer کوچک پاسخ را با همان Observationهای واقعی به زبان کاربر برمی‌گرداند، بدون افشای reasoning یا ترجمه داخلی.

3. **Adaptive Context Budget**
   - Context بین Conversation، Skill Manifest، Observationها و فضای خروجی تقسیم می‌شود.
   - در Context حدود 8K حداکثر 6 Skill مرتبط، 3 Observation اخیر و Observationهای حدود 2800 کاراکتری وارد Planning Prompt می‌شوند.
   - History به شکل Turn-aware فشرده می‌شود و وسط متن به صورت تصادفی بریده نمی‌شود.
   - خروجی خام Tool در Runtime از بین نمی‌رود؛ فقط نسخه فشرده وارد مدل می‌شود. مدل در صورت نیاز می‌تواند با web_find یا Skill دقیق‌تر داده بیشتری بگیرد.

4. **Activity UI حرفه‌ای**
   - تا زمان اجرای Task پنل Activity باز و دارای ارتفاع ثابت/اسکرول خودکار است.
   - بعد از پاسخ نهایی پنل خودکار جمع می‌شود و فقط خلاصه آخرین مرحله را نشان می‌دهد.
   - کاربر می‌تواند برای مشاهده جزئیات دوباره آن را باز کند.
   - به جای Workingهای تکراری، Route، Context policy، دسته Skillها، تصمیم مدل، Skill انتخابی، آرگومان‌های امن، زمان Tool، اندازه Observation، خطا و fallback نمایش داده می‌شود.

## Route نمونه

`سلام` → Direct model response → بدون Skill

`API چیست؟` → Direct model response → بدون Skill

`https://example.com را باز کن و بگو چیست` → Agent → web.read/web_check → Observation → Final

`جدیدترین قیمت ... را پیدا کن` → Agent → web search/read → Final

`در سایت وارد شو و دکمه ثبت را بزن` → Agent → Browser skills → Verify → Final
