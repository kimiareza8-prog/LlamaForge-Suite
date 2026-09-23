<?php
require __DIR__ . '/lib/bootstrap.php';
header('X-Robots-Tag: noindex, nofollow, noarchive');

if (!aib_agent_auth()) {
    aib_json_response([
        'ok'=>false,
        'error'=>'invalid_or_missing_token',
        'hint'=>'Use the exact LlamaForge Connection URL from manage.php or provide the Agent token as Bearer/X-AIB-Token.',
    ], 401);
}

$config = aib_config();
$token = (string)($config['agent_token'] ?? '');
$appId = 'aib_' . substr(hash('sha256', aib_base_url()), 0, 18);
aib_touch_agent('LlamaForge connector probe', ['state'=>'connected']);

aib_json_response([
    'ok'=>true,
    'protocol'=>'LlamaForge Remote App Sync',
    'protocol_version'=>'1.5',
    'app'=>[
        'id'=>$appId,
        'name'=>(string)($config['app_name'] ?? 'AI Bridge'),
        'version'=>(string)($config['version'] ?? '3.9.0-live-stream-files'),
        'base_url'=>aib_base_url(),
        'chat_url'=>aib_url('index.php'),
    ],
    'authentication'=>[
        'type'=>'bearer_or_x_aib_token',
        'header'=>'X-AIB-Token',
        'token_in_connection_url'=>true,
    ],
    'endpoints'=>[
        'poll'=>aib_url('agent.php',['action'=>'inbox','wait'=>(int)($config['agent_long_poll_seconds'] ?? 20),'claim'=>1,'limit'=>1]),
        'heartbeat'=>aib_url('agent.php',['action'=>'heartbeat']),
        'activity'=>aib_url('agent.php',['action'=>'activity']),
        'typing'=>aib_url('agent.php',['action'=>'typing']),
        'reply'=>aib_url('agent.php',['action'=>'reply']),
        'status'=>aib_url('api.php'),
        'bridge_update'=>aib_url('bridge-update.php'),
        'workspace_sync'=>aib_url('workspace-sync.php'),
    ],
    'capabilities'=>[
        'continuous_poll'=>true,
        'conversation_history'=>true,
        'live_activity'=>true,
        'partial_answer'=>true,
        'lease_heartbeat'=>true,
        'final_reply'=>true,
        'remote_model_selection'=>true,
        'remote_model_stop'=>true,
        'model_catalog_sync'=>true,
        'browser_scoped_history'=>true,
        'conversation_delete'=>true,
        'response_cancel'=>true,
        'remote_bridge_update'=>true,
        'remote_bridge_rollback'=>true,
        'calendar_workspace'=>true,
        'file_workspace'=>true,
        'workspace_sync'=>true,
    ],
    'instructions'=>[
        'Paste this Connection URL into LlamaForge > Agent > Connected websites/apps.',
        'LlamaForge stays connected even when no model is loaded so the website can request a model load.',
        'The website receives an opaque local-model catalog and can request Load/Switch without seeing local filesystem paths.',
        'Agent activity and partial answers are pushed back to this website in real time.',
        'After this updater-capable Bridge is installed once, future Bridge code updates can be installed or rolled back directly from LlamaForge.',
    ],
    'server_time'=>aib_now(),
]);
