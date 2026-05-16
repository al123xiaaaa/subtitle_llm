import unittest
from io import StringIO
from unittest.mock import patch

from src.services.file_handler import FileHandler


class TestFileHandler(unittest.TestCase):
    @patch('builtins.open')
    def test_read_srt_with_numeric_subtitle(self, mock_open_func):
        # 创建一个包含数字字幕的 SRT 内容
        srt_content = """1
00:00:01,000 --> 00:00:04,000
This is the first subtitle

2
00:00:05,000 --> 00:00:08,000
42

3
00:00:09,000 --> 00:00:12,000
This is the third subtitle
"""
        # 设置 mock_open 以返回我们的 SRT 内容
        mock_open_func.return_value = StringIO(srt_content)
        
        # 调用 read_srt 方法
        subtitle = FileHandler.read_srt("mock_file.srt")
        
        # 验证结果
        self.assertEqual(len(subtitle.entries), 3)
        self.assertEqual(subtitle.entries[0].text, "This is the first subtitle")
        self.assertEqual(subtitle.entries[1].text, "42")
        self.assertEqual(subtitle.entries[2].text, "This is the third subtitle")

if __name__ == '__main__':
    unittest.main()
