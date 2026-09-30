"""Local Telegram page contract; no Telegram account or network is needed."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from llamaforge.core.agent_tools import AgentPermissions
from llamaforge.core.telegram_skill import TelegramService
from llamaforge.core.workspace import FileWorkspace


class Vault:
    def load(self):
        return {'api_id': 1, 'api_hash': 'a' * 32, 'session': 'offline-test-session'}
    def save(self, value):
        pass
    def clear(self):
        pass


class Client:
    def __init__(self):
        self.peer = NS(id=17, first_name='Friend', last_name='', username='friend17', title=None, bot=False)
        self.sends = []
        self.file_sends = []
        self.message_queries = []
    async def connect(self):
        pass
    async def disconnect(self):
        pass
    async def is_user_authorized(self):
        return True
    async def get_me(self):
        return self.peer
    async def get_dialogs(self, limit):
        return [NS(entity=self.peer, id=17, name='Friend', unread_count=0)]
    async def get_messages(self, peer, **kwargs):
        self.message_queries.append(kwargs)
        if 'ids' in kwargs:
            number = kwargs['ids']
            if number == 9:
                return NS(id=9, raw_text='', media=NS(photo=True), photo=True,
                          file=NS(name=None, size=3, mime_type='image/jpeg'), date=None, out=False)
            return NS(id=number, raw_text=self.sends[-1] if self.sends else '',
                      media='file' if self.file_sends else None, date=None, out=True)
        return [NS(id=7 if kwargs.get('max_id') else 9, raw_text='', media=NS(photo=True), photo=True,
                   file=NS(name=None, size=3, mime_type='image/jpeg'), date=None, out=False)]
    async def send_message(self, peer, text, **kwargs):
        self.sends.append(text)
        return NS(id=100+len(self.sends), raw_text=text)
    async def send_file(self, peer, path, **kwargs):
        self.file_sends.append(Path(path).name)
        return NS(id=200+len(self.file_sends))
    async def iter_download(self, media, **kwargs):
        yield b'abc'


class TelegramPageTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.service = TelegramService(vault=Vault(), client_factory=lambda *_: self.client)
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = FileWorkspace(Path(self.temp.name) / 'workspace')
        self.permissions = AgentPermissions()
        self.ref = self.service.dashboard()['dialogs'][0]['chat_ref']
    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_explicit_ui_send_and_repeat_have_separate_receipts(self):
        for _ in range(2):
            result = self.service.ui_action({'operation':'send','chat_ref':self.ref,'text':'hello'}, self.permissions)
            self.assertTrue(result['verification']['verified'])
        self.assertEqual(self.client.sends, ['hello','hello'])
        with self.assertRaises(PermissionError):
            self.service.ui_action({'operation':'send','chat_ref':self.ref,'text':'blocked'},
                                   AgentPermissions(allow_telegram_write=False))
        self.assertEqual(len(self.client.sends), 2)

    def test_photo_metadata_download_and_file_send(self):
        messages = self.service.dashboard_messages(self.ref)['messages']
        self.assertEqual(messages[0]['media']['kind'], 'photo')
        result = self.service.tool({'operation':'download_media','chat_ref':self.ref,'message_id':9},
                                   self.permissions,'telegram-ui',self.workspace)
        self.assertEqual(result['file']['name'], 'tg-9-message-9.jpg')
        self.assertEqual(self.workspace._resolve_id(result['file']['id'])[1].read_bytes(), b'abc')
        sent = self.service.ui_action({'operation':'send_file','chat_ref':self.ref,'file_id':result['file']['id']},
                                      self.permissions,self.workspace)
        self.assertTrue(sent['sent'])
        self.assertEqual(len(self.client.file_sends), 1)
        with self.assertRaises(PermissionError):
            self.service.tool({'operation':'download_media','chat_ref':self.ref,'message_id':9},
                              AgentPermissions(allow_workspace_write=False),'telegram-ui',self.workspace)

    def test_older_messages_are_loaded_only_on_explicit_cursor_request(self):
        self.service.dashboard_messages(self.ref, limit=1)
        self.assertEqual(self.client.message_queries[-1], {'limit':1})
        older = self.service.dashboard_messages(self.ref, limit=1, before_id=9)
        self.assertEqual(self.client.message_queries[-1], {'limit':1,'max_id':9})
        self.assertEqual(older['messages'][0]['message_id'], 7)
        self.assertTrue(older['has_more'])


if __name__ == '__main__':
    unittest.main()
