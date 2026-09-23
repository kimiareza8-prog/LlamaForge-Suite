<?php
require __DIR__ . '/lib/bootstrap.php';
$config = aib_config();
$assetVersion = (string)max((int)@filemtime(__DIR__ . '/assets/app.css'), (int)@filemtime(__DIR__ . '/assets/app.js'));
$boot = [
    'appName' => (string)($config['app_name'] ?? 'AI Bridge'),
    'version' => (string)($config['version'] ?? '3.9.0-live-stream-files'),
    'apiUrl' => aib_url('api.php'),
    'pollMs' => (int)($config['chat_poll_ms'] ?? 850),
    'assetVersion' => $assetVersion,
];
?>
<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#ffffff">
<title><?=htmlspecialchars($boot['appName'])?></title>
<link rel="stylesheet" href="assets/app.css?v=<?=htmlspecialchars($boot['assetVersion'])?>">
</head>
<body>
<div class="app-shell">
    <aside class="sidebar" id="sidebar" aria-label="گفتگوها">
        <div class="sidebar-top">
            <div class="brand-row">
                <div class="brand-mark" aria-hidden="true">
                    <svg viewBox="0 0 24 24"><path d="M12 2.75a9.25 9.25 0 1 0 0 18.5 9.25 9.25 0 0 0 0-18.5Zm0 3.1a6.15 6.15 0 1 1 0 12.3 6.15 6.15 0 0 1 0-12.3Z"/><path d="M12 8.05a3.95 3.95 0 1 0 0 7.9 3.95 3.95 0 0 0 0-7.9Z"/></svg>
                </div>
                <div class="brand-copy"><strong><?=htmlspecialchars($boot['appName'])?></strong><span>Live Bridge</span></div>
                <button class="icon-btn close-sidebar" id="closeSidebar" aria-label="بستن منو">
                    <svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg>
                </button>
            </div>
            <button class="new-chat-btn" id="newChatBtn">
                <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>
                <span>گفتگوی جدید</span>
            </button>
            <nav class="workspace-nav" aria-label="بخش‌های کاری">
                <button type="button" class="workspace-nav-btn active" id="navChat"><span class="nav-glyph">◌</span><span>چت</span></button>
                <button type="button" class="workspace-nav-btn" id="navCalendar"><span class="nav-glyph">▦</span><span>تقویم</span></button>
                <button type="button" class="workspace-nav-btn" id="navFiles"><span class="nav-glyph">▤</span><span>فایل‌ها</span></button>
            </nav>
        </div>
        <div class="conversation-label">گفتگوهای اخیر</div>
        <nav class="conversation-list" id="conversationList"></nav>
        <div class="sidebar-footer">
            <button class="sidebar-action danger-soft" id="clearHistoryBtn" type="button" aria-label="پاک کردن همه تاریخچه">
                <svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14"/></svg><span>پاک کردن تاریخچه</span>
            </button>
            <div class="live-status" id="sidebarStatus"><span class="status-dot"></span><span>در حال اتصال</span></div>
            <div class="bridge-version">Web v<?=htmlspecialchars($boot['version'])?> · Core <span id="agentVersion">—</span></div>
        </div>
    </aside>

    <div class="sidebar-scrim" id="sidebarScrim"></div>

    <main class="main-panel">
        <header class="topbar">
            <button class="icon-btn menu-btn" id="menuBtn" aria-label="باز کردن منو">
                <svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
            </button>
            <div class="topbar-title">
                <strong id="viewTitle"><?=htmlspecialchars($boot['appName'])?></strong>
                <span id="topStatus">در حال اتصال…</span>
            </div>
            <div class="web-model-picker" id="webModelPicker">
                <select id="modelSelect" aria-label="انتخاب مدل" disabled><option value="">در حال دریافت مدل‌ها…</option></select>
                <span id="modelLoadState"></span>
                <button class="model-stop-btn" id="modelStopBtn" type="button" aria-label="توقف و Unload مدل" title="توقف و آزاد کردن مدل" disabled>
                    <svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>
                </button>
            </div>
            <button class="icon-btn new-mobile" id="newMobileBtn" aria-label="گفتگوی جدید">
                <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>
            </button>
        </header>

        <section class="chat-scroll view-panel active" id="chatScroll" data-view="chat">
            <div class="chat-column" id="chatColumn">
                <div class="empty-state" id="emptyState">
                    <div class="empty-orb" aria-hidden="true"><span></span></div>
                    <h1>چه کمکی می‌خواهی؟</h1>
                    <p>پیامت را بفرست. Calendar و File Agent فقط وقتی لازم باشند وارد عمل می‌شوند.</p>
                </div>
                <div class="messages" id="messages"></div>
            </div>
        </section>

        <section class="workspace-view view-panel" id="calendarView" data-view="calendar" hidden>
            <div class="workspace-page calendar-page">
                <div class="workspace-hero">
                    <div><span class="workspace-kicker">Calendar Agent</span><h1>تقویم شمسی</h1><p id="calendarTodayText">—</p></div>
                    <div class="workspace-hero-clock" id="calendarClock">--:--</div>
                </div>
                <div class="calendar-layout">
                    <div class="calendar-card">
                        <div class="calendar-toolbar">
                            <div class="cal-nav-group"><button type="button" id="calNext">‹</button><button type="button" id="calToday">امروز</button><button type="button" id="calPrev">›</button></div>
                            <h2 id="calendarMonthTitle">—</h2>
                        </div>
                        <div class="calendar-weekdays"><span>شنبه</span><span>یکشنبه</span><span>دوشنبه</span><span>سه‌شنبه</span><span>چهارشنبه</span><span>پنجشنبه</span><span>جمعه</span></div>
                        <div class="calendar-grid" id="calendarGrid"></div>
                    </div>
                    <aside class="calendar-side">
                        <div class="side-card">
                            <div class="side-card-head"><h3>برنامه‌های نزدیک</h3><span>هوشمند</span></div>
                            <div id="upcomingEvents" class="upcoming-list"></div>
                        </div>
                        <div class="side-card quick-event-card">
                            <div class="side-card-head"><h3>ثبت سریع</h3><span id="selectedJalaliDate">امروز</span></div>
                            <input id="quickEventTitle" type="text" maxlength="300" placeholder="عنوان؛ مثلاً جلسه با شرکت">
                            <div class="quick-event-row"><input id="quickEventTime" type="time" value="10:00"><select id="quickEventDuration"><option value="30">۳۰ دقیقه</option><option value="60" selected>۱ ساعت</option><option value="90">۱.۵ ساعت</option><option value="120">۲ ساعت</option></select></div>
                            <select id="quickEventReminder"><option value="0">بدون یادآوری</option><option value="10">۱۰ دقیقه قبل</option><option value="30" selected>۳۰ دقیقه قبل</option><option value="60">۱ ساعت قبل</option><option value="1440">۱ روز قبل</option></select>
                            <textarea id="quickEventNotes" rows="2" maxlength="1200" placeholder="توضیح اختیاری"></textarea>
                            <button type="button" class="workspace-primary" id="saveQuickEvent">ثبت در تقویم</button>
                            <p class="workspace-hint">برای سؤال‌هایی مثل «اولین وقت خالی من کیه؟» Skill جدا وجود ندارد؛ مدل با خواندن زمان و رویدادهای همین تقویم خودش نتیجه را استنتاج می‌کند.</p>
                        </div>
                    </aside>
                </div>
            </div>
        </section>

        <section class="workspace-view view-panel" id="filesView" data-view="files" hidden>
            <div class="workspace-page files-page">
                <div class="workspace-hero">
                    <div><span class="workspace-kicker">File Manager Agent</span><h1>فایل‌ها و اسناد</h1><p>مدیریت روی هاست + همگام‌سازی Workspace با LlamaForge محلی</p></div>
                    <div class="workspace-sync-badge"><span></span>Host ↔ Local</div>
                </div>
                <div class="files-card">
                    <div class="files-toolbar">
                        <div class="file-actions"><button type="button" class="workspace-primary" id="workspaceUploadBtn">آپلود فایل</button><button type="button" class="workspace-secondary" id="newFolderBtn">پوشه جدید</button><input id="workspaceFileInput" type="file" multiple hidden></div>
                        <div class="file-search"><input id="workspaceSearch" type="search" placeholder="جستجو در نام، مسیر و توضیحات…"></div>
                    </div>
                    <div class="file-breadcrumbs" id="fileBreadcrumbs"></div>
                    <div class="file-table-head"><span>نام</span><span>نوع</span><span>حجم</span><span>عملیات</span></div>
                    <div class="file-list" id="workspaceFileList"></div>
                    <div class="file-empty" id="workspaceFileEmpty" hidden>این پوشه خالی است.</div>
                </div>
                <p class="workspace-hint files-hint">Agent ابتدا فقط Metadata را می‌بیند. اگر بگویی «این را بگذار در مدارک شرکت»، فایل خوانده نمی‌شود؛ فقط وقتی بگویی «این فایل چیست؟» یا پاسخ واقعاً به محتوای آن نیاز داشته باشد، عملیات read_content انجام می‌شود.</p>
            </div>
        </section>

        <footer class="composer-wrap" id="composerWrap">
            <div class="attachment-tray" id="attachmentTray" hidden></div>
            <div class="composer" id="composer">
                <textarea id="messageInput" rows="1" maxlength="5000" placeholder="پیام بده…" autocomplete="off"></textarea>
                <div class="composer-actions">
                    <button class="attach-btn" id="attachBtn" type="button" aria-label="پیوست فایل یا عکس" title="پیوست فایل یا عکس">
                        <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>
                    </button>
                    <input id="fileInput" type="file" multiple  hidden>
                    <div class="connection-pill" id="connectionPill"><span></span><b>Live</b></div>
                    <button class="send-btn stop-reply-btn" id="stopReplyBtn" aria-label="توقف پاسخ" title="توقف پاسخ" hidden>
                        <svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>
                    </button>
                    <button class="send-btn" id="sendBtn" aria-label="ارسال" disabled>
                        <svg viewBox="0 0 24 24"><path d="M12 19V5M6.5 10.5 12 5l5.5 5.5"/></svg>
                    </button>
                </div>
                <input type="text" name="website" id="websiteField" tabindex="-1" autocomplete="off" aria-hidden="true">
            </div>
            <div class="composer-note"><span id="attachmentHint">Enter برای ارسال · Shift+Enter برای خط جدید</span></div>
        </footer>
    </main>
</div>
<button class="scroll-bottom-btn" id="scrollBottomBtn" type="button" aria-label="رفتن به آخر گفتگو" hidden>
    <svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>
</button>
<div id="modalRoot"></div>
<script>window.AIB_BOOT = <?=json_encode($boot, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)?>;</script>
<script src="assets/app.js?v=<?=htmlspecialchars($boot['assetVersion'])?>"></script>
</body>
</html>
