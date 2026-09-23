# LlamaForge Agent Core v2

در نسخه 0.20 معماری Agent از نو نوشته شده است.

## چرخه اجرا

هر درخواست Agent این مسیر را طی می‌کند:

1. **Understand** — اولین inference روی خود مدل لوکال انجام می‌شود؛ هیچ URL قبل از تصمیم مدل خودکار خوانده نمی‌شود.
2. **Choose Skill** — مدل فهرست Skillهای واقعی و توضیح هرکدام را می‌بیند و دقیقاً یک Skill را انتخاب می‌کند.
3. **Execute** — LlamaForge همان Skill را اجرا می‌کند.
4. **Observe** — خروجی واقعی ابزار به شکل فشرده به مدل برگردانده می‌شود.
5. **Re-plan** — مدل با توجه به Observation تصمیم می‌گیرد Skill دیگری لازم است یا کار تمام شده است.
6. **Final** — پاسخ نهایی فقط بعد از Observationهای واقعی ساخته می‌شود.

## چرا native tool calling حذف شد؟

Agent Core v2 برای تصمیم‌گیری به parser ابزارهای `llama.cpp` وابسته نیست. تصمیم کنترل به صورت JSON ساده از یک chat completion عادی گرفته می‌شود. این روش با Chat Templateهای سخت‌گیر Gemma/Qwen/Mistral نیز سازگارتر است. اگر runtime از JSON response mode پشتیبانی کند LlamaForge از آن استفاده می‌کند؛ در runtimeهای قدیمی خودکار بدون آن retry می‌کند.

## Skillهای داخلی

- `web_read`: خواندن مستقیم URL و استخراج متن، لینک و فرم.
- `web_search`: جست‌وجوی وب.
- `http_request`: GET/POST/PUT/PATCH/DELETE برای APIها؛ متدهای تغییردهنده نیازمند Write permission هستند.
- `browser_open`, `browser_snapshot`: سایت‌های JavaScript.
- `browser_click`, `browser_type`: تعامل واقعی با صفحه در صورت فعال بودن Write permission.
- `connector_call`: عملیات OpenAPI ثبت‌شده.
- `skill_*`: Skillهای JSON سفارشی کاربر.

## نکته برای مدل‌های کوچک

Observationها قبل از برگشت به مدل فشرده می‌شوند تا مدل‌های 4B با context محدود با یک صفحه بسیار بزرگ از کار نیفتند. اگر مدل JSON نامعتبر تولید کند یک repair inference خودکار انجام می‌شود و در نهایت فقط برای جلوگیری از توقف کامل، یک router ساده می‌تواند اولین URL واضح را به `web_read` بدهد.
