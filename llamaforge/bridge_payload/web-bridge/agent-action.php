<?php
require __DIR__ . '/lib/bootstrap.php';
header('X-Robots-Tag: noindex, nofollow, noarchive');

// Browser-compatible, capability-scoped action endpoint.
// It intentionally does NOT accept the master Agent token.
$in = aib_input();
$op = strtolower(trim((string)($in['op'] ?? 'reply')));
$id = trim((string)($in['message_id'] ?? ''));
$cap = trim((string)($in['cap'] ?? ''));
$agentName = aib_clean_message((string)($in['agent_name'] ?? 'Web Agent'), 120);

if (!aib_is_uuid($id) || $cap === '') {
    aib_json_response(['ok'=>false,'error'=>'message_id_and_cap_required'], 400);
}

$message = aib_read_message($id);
if (!$message) aib_json_response(['ok'=>false,'error'=>'message_not_found'], 404);
if (!aib_validate_reply_capability($message, $cap)) {
    aib_json_response([
        'ok'=>false,
        'error'=>'invalid_or_expired_reply_capability',
        'hint'=>'Reopen the Agent inbox URL to claim the message again and receive a fresh scoped capability.',
    ], 403);
}
if (($message['status'] ?? '') === 'cancelled' || !empty($message['cancel_requested'])) {
    aib_json_response(['ok'=>false,'error'=>'cancelled_by_user'],409);
}
if (($message['status'] ?? '') === 'answered') {
    aib_json_response(['ok'=>true,'state'=>'already_answered','message_id'=>$id]);
}

$config = aib_config();
$maxAnswer = max(2000, (int)($config['max_answer_chars'] ?? 50000));
$maxGetChunk = max(300, (int)($config['browser_get_chunk_chars'] ?? 1400));

if ($op === 'append' || $op === 'typing') {
    $chunk = str_replace("\0", '', (string)($in['text'] ?? $in['chunk'] ?? ''));
    if (function_exists('mb_substr')) $chunk = mb_substr($chunk, 0, $maxGetChunk);
    else $chunk = substr($chunk, 0, $maxGetChunk);
    if ($chunk === '') aib_json_response(['ok'=>false,'error'=>'text_required'], 400);
    $updated = aib_update_message($id, function($row) use ($cap, $chunk, $agentName, $maxAnswer) {
        if (($row['status'] ?? '') === 'cancelled' || !empty($row['cancel_requested'])) return $row;
        if (!aib_validate_reply_capability($row, $cap)) return $row;
        $draft = (string)($row['reply_draft'] ?? '');
        $draft .= $chunk;
        if (function_exists('mb_substr')) $draft = mb_substr($draft, 0, $maxAnswer);
        else $draft = substr($draft, 0, $maxAnswer);
        $row['reply_draft'] = $draft;
        $row['partial_answer'] = $draft;
        $row['status'] = 'processing';
        $row['claimed_at'] = aib_now();
        $row['agent_name'] = $agentName ?: 'Web Agent';
        return $row;
    });
    if (!$updated || !aib_validate_reply_capability($updated, $cap)) {
        aib_json_response(['ok'=>false,'error'=>'capability_changed_or_expired'], 409);
    }
    aib_touch_agent($agentName ?: 'Web Agent');
    aib_json_response([
        'ok'=>true,
        'state'=>'draft_appended',
        'message_id'=>$id,
        'draft_chars'=>function_exists('mb_strlen') ? mb_strlen((string)($updated['reply_draft'] ?? '')) : strlen((string)($updated['reply_draft'] ?? '')),
        'next'=>'Append another chunk with the same capability, or call op=finish to publish the accumulated draft.',
    ]);
}

if ($op === 'finish') {
    $updated = aib_update_message($id, function($row) use ($cap, $agentName) {
        if (($row['status'] ?? '') === 'cancelled' || !empty($row['cancel_requested'])) return $row;
        if (!aib_validate_reply_capability($row, $cap)) return $row;
        $draft = trim((string)($row['reply_draft'] ?? ''));
        if ($draft === '') return $row;
        $row['answer'] = $draft;
        $row['partial_answer'] = null;
        $row['status'] = 'answered';
        $row['agent_name'] = $agentName ?: 'Web Agent';
        $row['answered_at'] = aib_now();
        aib_invalidate_reply_capability($row);
        return $row;
    });
    if (!$updated) aib_json_response(['ok'=>false,'error'=>'message_not_found'], 404);
    if (($updated['status'] ?? '') !== 'answered') aib_json_response(['ok'=>false,'error'=>'empty_draft_or_capability_expired'], 400);
    aib_touch_agent($agentName ?: 'Web Agent');
    aib_json_response([
        'ok'=>true,
        'state'=>'answered',
        'saved'=>true,
        'message_id'=>$id,
        'critical_next_step'=>'Return to the next_wait_url that was supplied with this message and keep waiting. Do not stop after this reply.',
    ]);
}

if ($op === 'reply') {
    $answer = aib_clean_message((string)($in['answer'] ?? $in['text'] ?? ''), $maxAnswer);
    if ($answer === '') aib_json_response(['ok'=>false,'error'=>'answer_required'], 400);
    $updated = aib_update_message($id, function($row) use ($cap, $answer, $agentName) {
        if (($row['status'] ?? '') === 'cancelled' || !empty($row['cancel_requested'])) return $row;
        if (!aib_validate_reply_capability($row, $cap)) return $row;
        $row['answer'] = $answer;
        $row['partial_answer'] = null;
        $row['status'] = 'answered';
        $row['agent_name'] = $agentName ?: 'Web Agent';
        $row['answered_at'] = aib_now();
        aib_invalidate_reply_capability($row);
        return $row;
    });
    if (!$updated) aib_json_response(['ok'=>false,'error'=>'message_not_found'], 404);
    if (($updated['status'] ?? '') !== 'answered') aib_json_response(['ok'=>false,'error'=>'capability_changed_or_expired'], 409);
    aib_touch_agent($agentName ?: 'Web Agent');
    aib_json_response([
        'ok'=>true,
        'state'=>'answered',
        'saved'=>true,
        'message_id'=>$id,
        'critical_next_step'=>'Return to the next_wait_url that was supplied with this message and keep waiting. Do not stop after this reply.',
    ]);
}

aib_json_response([
    'ok'=>false,
    'error'=>'unsupported_operation',
    'allowed_ops'=>['reply','append','finish','typing'],
], 400);
