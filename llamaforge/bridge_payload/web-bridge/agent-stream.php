<?php
require __DIR__ . '/lib/bootstrap.php';
if (!aib_agent_auth()) { http_response_code(401); exit('Unauthorized'); }

@set_time_limit(240);
ignore_user_abort(true);
header('Content-Type: text/event-stream; charset=utf-8');
header('Cache-Control: no-cache, no-transform');
header('X-Accel-Buffering: no');
header('Connection: keep-alive');

function sse_send(string $event, array $payload): void {
    echo "event: {$event}\n";
    echo 'data: ' . json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n\n";
    @ob_flush(); @flush();
}

$config = aib_config();
$start = time();
$sent = [];
aib_touch_agent('SSE Agent');
sse_send('connected', [
    'protocol'=>'AI Bridge Agent Protocol 3.0',
    'instruction'=>'Keep this stream open. When a message event arrives, answer it using either standard_api_reply or web_get_action, then continue listening.',
]);

while (!connection_aborted() && time() - $start < 220) {
    aib_touch_agent('SSE Agent');
    foreach (aib_pending_messages(5, true) as $m) {
        if (isset($sent[$m['id']])) continue;
        $sent[$m['id']] = true;
        $cap = (string)($m['_reply_capability'] ?? '');
        $replyGet = $cap !== '' ? aib_url('agent-action.php',[
            'op'=>'reply','message_id'=>$m['id'],'cap'=>$cap,'agent_name'=>'SSE Web Agent','answer'=>'__URL_ENCODED_ANSWER__'
        ]) : null;
        $appendGet = $cap !== '' ? aib_url('agent-action.php',[
            'op'=>'append','message_id'=>$m['id'],'cap'=>$cap,'agent_name'=>'SSE Web Agent','text'=>'__URL_ENCODED_CHUNK__'
        ]) : null;
        $finishGet = $cap !== '' ? aib_url('agent-action.php',[
            'op'=>'finish','message_id'=>$m['id'],'cap'=>$cap,'agent_name'=>'SSE Web Agent'
        ]) : null;
        sse_send('message', [
            'message_id'=>$m['id'],
            'session_id'=>$m['session_id'],
            'user_message'=>$m['question'],
            'conversation_history'=>aib_history_for_agent($m['session_id']),
            'standard_api_reply'=>[
                'method'=>'POST',
                'url'=>aib_url('agent.php',['token'=>$config['agent_token'],'action'=>'reply']),
            ],
            'web_get_action'=>[
                'short_reply_url_template'=>$replyGet,
                'append_url_template'=>$appendGet,
                'finish_url'=>$finishGet,
            ],
        ]);
    }
    echo ": keepalive " . time() . "\n\n";
    @ob_flush(); @flush();
    sleep(2);
}
sse_send('reconnect', [
    'instruction'=>'Reconnect immediately to continue waiting.',
    'url'=>aib_url('agent-stream.php',['token'=>$config['agent_token']]),
]);
