<?php
// Example only. On first request the bridge creates data/config.php automatically
// with unique random secrets. Do not commit the generated config.php.
return [
    'version' => '3.9.0-live-stream-files',
    'app_name' => 'AI Bridge',
    'agent_token' => 'GENERATED_ON_FIRST_RUN',
    'owner_key' => 'GENERATED_ON_FIRST_RUN',
    'browser_get_reply' => true,
    'agent_long_poll_seconds' => 20,
    'processing_lease_seconds' => 90,
    'chat_poll_ms' => 850,
    'max_message_chars' => 5000,
    'max_answer_chars' => 50000,
    'reply_capability_ttl_seconds' => 900,
    'browser_get_chunk_chars' => 1400,
    'max_activity_events' => 80,
];
