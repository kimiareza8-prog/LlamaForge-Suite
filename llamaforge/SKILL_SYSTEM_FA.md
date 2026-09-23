# معماری Smart Skill Tree v4

مسیر جدید:

`User → Intent Guard + Local Model Router → Capability Families → Shortlist → Planner → Preflight → Execute → Observe → Verify → Final`

## ایدهٔ اصلی

Skillها دیگر «یک Skill برای هر سؤال» نیستند. مدل ابتدا سؤال را به چند **قابلیت عمومی** می‌شکند و بعد از Primitiveهای همان شاخه استفاده می‌کند. شاخه‌های اصلی عبارت‌اند از:

- **Live Web**: جستجو، خواندن صفحه، بررسی URL و دانلود.
- **Time & Calendar**: زمان واقعی سیستم، تاریخ میلادی/شمسی، لیست رویدادها و ساخت/ویرایش قرار.
- **Files & Attachments**: فایل پیوست، Workspace، PDF/Office/ZIP، ذخیره و ویرایش متن/کد.
- **Browser**: فقط برای JavaScript، لاگین، فرم، کلیک و تایپ.
- **API**: HTTP و Endpointهای صریح.
- **Extensions**: Connector و Custom Skill.

## چرا دو لایهٔ تصمیم داریم؟

مدل لوکال همچنان تصمیم‌گیرندهٔ اصلی است، اما مدل‌های کوچک گاهی درخواست واضحی مثل «امروز چندمه؟» یا «این فایل را بخوان» را Direct Chat تشخیص می‌دهند و بعد می‌گویند دسترسی ندارند. برای جلوگیری از این بن‌بست، Runtime فقط در سطح **Capability Family** چند Guard قطعی دارد؛ نه در سطح پاسخ یا Skill دقیق.

مثال:

- «امروز چندمه؟» → حداقل شاخهٔ Calendar باید دیده شود.
- فایل پیوست + «بخون» → شاخهٔ Files باید دیده شود و Attachment ID گم نمی‌شود.
- URL → شاخهٔ Web باید دیده شود.
- «روی سایت کلیک کن» → Browser هم اضافه می‌شود.

بعد از آن، مدل هنوز خودش انتخاب می‌کند چه Primitiveای اجرا شود و می‌تواند چند شاخه را ترکیب کند.

## Permissionهای جدا

دو نوع Write دیگر با هم قاطی نیستند:

1. **Local calendar & file changes** — پیش‌فرض روشن؛ فقط Workspace محلی و تقویم خود LlamaForge را تغییر می‌دهد.
2. **External site actions** — پیش‌فرض خاموش؛ POST/PUT/PATCH/DELETE و click/type/select روی سرویس‌ها و سایت‌های بیرونی.

بنابراین خاموش بودن دسترسی خطرناک سایت دیگر باعث نمی‌شود مدل بگوید «نمی‌توانم فایل را بخوانم» یا «نمی‌توانم برای فردا رویداد بسازم».

## File Manager عمومی

`workspace_files` یک Capability عمومی است و عملیات زیر را ترکیب می‌کند:

- `list`, `search`, `metadata`
- `read_content`
- `store_attachment`
- `write_text`, `replace_text`
- `mkdir`, `move`, `rename`
- `trash`, `restore`, `delete`

`read_content` برای TXT/MD/JSON/CSV و فایل‌های کد، Office، PDF و ZIP پشتیبانی دارد. ZIP بدون Extract روی فایل‌سیستم بررسی می‌شود و فقط File Tree و Preview محدود از فایل‌های متنی/کد به مدل داده می‌شود.

## Calendar عمومی

`calendar` همچنان یک Capability عمومی است:

- `now`
- `convert`
- `month`
- `list`
- `create`, `update`, `cancel`, `delete`

برای مثال «وقت خالی من فردا چه ساعتی است؟» Skill جدا ندارد؛ مدل `now/list` را اجرا می‌کند و خودش از Busy Intervalها نتیجه می‌گیرد.

## Recovery

- فراخوانی شکست‌خوردهٔ یکسان دوباره اجرا نمی‌شود.
- Browser برای صفحهٔ عادی قبل از Web Read استفاده نمی‌شود.
- Attachment پیوست‌شده در Inbox با Workspace معمولی اشتباه گرفته نمی‌شود.
- اگر Planner روی درخواست واضح قبل از هر Observation جواب Final بدهد، Runtime اولین Primitive امن را اجرا می‌کند تا مدل به‌جای حدس، Observation واقعی ببیند.
