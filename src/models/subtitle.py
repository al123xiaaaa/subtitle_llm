from .subtitle_entry import SubtitleEntry


class Subtitle:
    def __init__(self):
        self.entries = []

    def add_entry(self, entry):
        if isinstance(entry, SubtitleEntry):
            self.entries.append(entry)
        else:
            raise TypeError("Entry must be an instance of SubtitleEntry")

    def get_entry(self, index):
        return self.entries[index]

    def remove_entry(self, index):
        del self.entries[index]

    # 重新排序
    def reorder_entries(self):
        # index 索引重新由1开始
        for i, entry in enumerate(self.entries):
            entry.index = i + 1

    def __len__(self):
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __str__(self):
        return "\n\n".join(str(entry) for entry in self.entries)
