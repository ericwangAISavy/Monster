"""Built-in companion identities; no assets, downloads or executable preferences."""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class CharacterStyle:
    label: str
    shell: tuple[str, str, str, str]
    face: tuple[str, str, str]
    detail: str
    inner: str


CHARACTERS = MappingProxyType({
    "monster": CharacterStyle("Pearl monster", ("#fff4f5", "#f1dff6", "#d5b5ed", "#a78bcb"),
                              ("#fff6fb", "#f5e9fa", "#e9d4f4"), "#d4aff2", "#ffe6ec"),
    "monkey": CharacterStyle("Cocoa monkey", ("#ffeedc", "#d9b49c", "#bd8d79", "#916757"),
                             ("#fff5e9", "#ffead8", "#f3cfbc"), "#936954", "#efb7bd"),
    "rabbit": CharacterStyle("Rose rabbit", ("#ffffff", "#fff0f5", "#f7d6e7", "#d9afcf"),
                             ("#fffaff", "#fff3fa", "#f6e3f3"), "#e5adc9", "#f5adc6"),
    "tiger": CharacterStyle("Peach tiger", ("#fff2d8", "#ffd3a3", "#f6ac7b", "#d88c64"),
                            ("#fff9e9", "#fff0d8", "#ffe0bf"), "#a56b60", "#f5b6bf"),
    "cat": CharacterStyle("Lavender cat", ("#fdf5ff", "#e8dcff", "#c7b5ee", "#a18bc9"),
                          ("#fffaff", "#f4ecff", "#e8dcfa"), "#9478be", "#f0b2d6"),
    "pigeon": CharacterStyle("Cloud pigeon", ("#f1fbff", "#cbddec", "#a8bfd9", "#819ab8"),
                             ("#ffffff", "#eff8ff", "#dfeaf8"), "#8e9dc8", "#ffc7ad"),
    "ox": CharacterStyle("Matcha ox", ("#fffff1", "#edf0d6", "#d2ddbb", "#a7ba94"),
                         ("#fff8ed", "#ffecd9", "#f4d7bd"), "#a1b48e", "#edbbc5"),
})
