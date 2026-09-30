const assert = require('node:assert/strict');
const {SSEDecoder, EventCursor, contextValue, draftValue, brainActivity} = require('../llamaforge/web/static/stream_protocol.js');
const parser = new SSEDecoder();
assert.deepEqual(parser.push('id: 1\r\ndata: {"delta":"ha"}\r'), []);
assert.deepEqual(parser.push('\n\r\nid: 1\ndata: {"delta":"ha"}\n\n'), [{delta:'ha'}]);
assert.deepEqual(parser.push('id: 2\ndata: {"delta":"ha"}\n\ndata: [DONE]\n\n'), [{delta:'ha'}]);
assert.equal(parser.done, true);
assert.throws(()=>new SSEDecoder().push('data: broken\n\n'));
const cursor = new EventCursor();
assert.equal(cursor.accept({revision:3}),true);
assert.equal(cursor.accept({revision:3}),false);
assert.equal(cursor.accept({revision:2}),false);
assert.equal(cursor.accept({type:'resync',revision:1}),true);
assert.equal(cursor.accept({revision:2}),true);
assert.equal(draftValue({ctx:''},'ctx',8192),'');
assert.equal(draftValue({ctx:'80'},'ctx',8192),'80');
assert.equal(contextValue('8192',16384),8192);
assert.throws(()=>contextValue('',16384));
assert.throws(()=>contextValue('8192.5',16384));
console.log('SSE split/dedup, reconnect cursor, and context draft: passed');
assert.equal(brainActivity({job:{state:'running',stage:'train'}},true).locked, true);
assert.equal(brainActivity({job:{state:'cancelling',stage:'train'}},false).locked, true);
for(const state of ['done','error','cancelled']){
  const final = brainActivity({job:{state,stage:'complete'}},true);
  assert.equal(final.pending,false);
  assert.equal(final.locked,false);
}
assert.equal(brainActivity({job:{state:'running',stage:'auto-setup'}},false).locked,false);
console.log('Brain completion/cancel/error releases the composer: passed');
