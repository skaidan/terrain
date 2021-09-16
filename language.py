import random
import json
import re
from collections import defaultdict


def choose(lst, exponent=2):
    x = random.random() ** exponent
    return lst[int(x * len(lst))]


class Language(object):
    def __init__(self, phonemes, syll="CVC", ortho={}, wordlength=(1, 4), restricts=[]):
        self.phonemes = {}
        for k, v in phonemes.items():
            v = list(v)
            random.shuffle(v)
            self.phonemes[k] = v
        self.syll = syll
        self.ortho = ortho
        self.wordlength = wordlength
        self.morphemes = defaultdict(list)
        self.allmorphemes = set()
        self.words = defaultdict(list)
        self.restricts = restricts
        self.genitive = self.morpheme("of", 3)
        self.definite = self.morpheme("the", 3)
        self.joiner = random.choice("   -")
        self.minlength = 6
        self.used = []
        self.last_n = []

    def syllable(self):
        while True:
            phones = []
            for s in self.syll:
                if s == "?":
                    if random.random() > 0.5:
                        phones = phones[:-1]
                else:
                    p = choose(self.phonemes[s], 1.5)
                    phones.append(p)
            syll = "".join(phones)
            for r in self.restricts:
                if re.search(r, syll):
                    break
            else:
                return syll

    def orthosyll(self):
        s = self.syllable()
        o = ""
        for c in s:
            o += self.ortho.get(c, c.lower())
        return o

    def morpheme(self, key=None, maxlength=None):
        morphemes = self.morphemes[key]
        n = random.randrange(len(morphemes) + (10 if key is None else 1))
        if n < len(morphemes):
            return morphemes[n]
        for _ in range(100):
            s = self.orthosyll()
            if maxlength and len(s) > maxlength:
                continue
            if s not in self.allmorphemes:
                break
        morphemes.append(s)
        self.allmorphemes.add(s)
        return s

    def word(self, key=None):
        ws = self.words[key]
        while True:
            n = random.randrange(len(ws) + (3 if key is None else 2))
            if n < len(ws):
                if ws[n] in self.last_n:
                    continue
                self.last_n.append(ws[n])
                self.last_n = self.last_n[-3:]
                return ws[n]
            l = random.randrange(*self.wordlength)
            keys = [key] + [None for _ in range(l - 1)]
            random.shuffle(keys)
            w = "".join(self.morpheme(k) for k in keys)
            ws.append(w)
            self.last_n.append(w)
            self.last_n = self.last_n[-3:]
            return w

    def name(self, key=None, genitive=0.5, definite=0.1, minlength=5, maxlength=12):
        while True:
            if genitive > random.random():
                x = random.random()
                w1 = self.word(key if random.random() < 0.6 else None).capitalize()
                w2 = self.word(key if random.random() < 0.6 else None).capitalize()
                if w1 == w2:
                    continue
                if random.random() > 0.5:
                    p = self.joiner.join([w1, self.genitive, w2])
                else:
                    p = self.joiner.join([w1, w2])
            else:
                p = self.word(key).capitalize()
            if random.random() < definite:
                p = self.joiner.join([self.definite, p])
            if not hasattr(self, "used"):
                self.used = []
            for p2 in self.used:
                if p in p2 or p2 in p:
                    break
            else:
                if minlength <= len(p) <= maxlength:
                    self.used.append(p)
                    return p


# vsets = ["AIU", "AEIOU", "AEIOUaei", "AEIOUu", "AIUai", "EOU", "AEIOU@0u"]
# csets = [
#     "PTKMNSL",
#     "PTKBDGMNLRSsZzc",
#     "PTKMNH",
#     "HKLMNPW'",
#     "PTKQVSGRMNnLJ",
#     "TKSsDBQgxMNLRWY",
#     "TKDGMNSs",
#     "PTKBDGMNzSZcHJW",
# ]
# lsets = ["RL", "R", "L", "WY", "RLWY"]
# ssets = ["S", "Ss", "SsF"]
# fsets = ["MN", "SK", "MNn", "SsZz"]
# syllsets = [
#     "CVV?C",
#     "CVC",
#     "CVVC?",
#     "CVC?",
#     "CV",
#     "VC",
#     "CVF",
#     "C?VC",
#     "CVF?",
#     "CL?VC",
#     "CL?VF",
#     "S?CVC",
#     "S?CVF",
#     "S?CVC?",
#     "C?VF",
#     "C?VC?",
#     "C?VF?",
#     "C?L?VC",
#     "VC",
#     "CVL?C?",
#     "C?VL?C",
#     "C?VLC?",
# ]
# vorthos = [
#     {"a": "á", "e": "é", "i": "í", "u": "ü", "@": "ä", "0": "ö"},
#     {"a": "au", "e": "ei", "i": "ie", "u": "oo", "@": "ea", "0": "ou"},
#     {"a": "â", "e": "ê", "i": "y", "u": "w", "@": "à", "0": "ô"},
#     {"a": "aa", "e": "ee", "i": "ii", "u": "uu", "@": "ai", "0": "oo"},
# ]
# corthos = [
#     {"n": "ng", "x": "kh", "s": "sh", "g": "gh", "z": "zh", "c": "ch"},
#     {"n": "ñ", "x": "x", "s": "š", "g": "gh", "z": "ž", "c": "č"},
#     {"n": "ng", "x": "ch", "s": "sch", "g": "gh", "z": "ts", "c": "tsch"},
#     {"n": "ng", "x": "c", "s": "ch", "g": "gh", "z": "j", "c": "tch"},
#     {"n": "ng", "x": "c", "s": "x", "g": "g", "z": "zh", "c": "q"},
# ]
# restricts = ["Ss", "sS", "LR", "RL", "FS", "Fs", "SS", "ss", r"(.)\1"]


def get_language(family="base"):
    with open(f"language_families/{family}.json") as fp:
        family = json.load(fp)

    while True:
        cset = choose(family["csets"])
        vset = choose(family["vsets"])
        syll = choose(family["syllsets"], 1)
        if len(cset) ** syll.count("C") * len(vset) * syll.count("V") > 30:
            break

    fset = choose([cset, random.choice(family["fsets"]), cset + random.choice(family["fsets"])])
    lset = choose(family["lsets"])
    sset = choose(family["ssets"])
    ortho = {"'": "`"}
    ortho.update(choose(family["vorthos"]))
    ortho.update(choose(family["corthos"]))
    minlength = random.choice([1, 2])
    if len(syll) < 3:
        minlength += 1
    maxlength = random.randrange(minlength + 1, 7)

    l = Language(
        phonemes={"V": vset, "C": cset, "L": lset, "F": fset, "S": sset},
        syll=syll,
        ortho=ortho,
        restricts=family["restricts"],
        wordlength=(minlength, maxlength),
    )
    return l


def show_language(l):
    print(l.phonemes["V"], l.phonemes["C"])
    if "F" in l.syll:
        print(
            l.phonemes["F"],
        )
    if "L" in l.syll:
        print(
            l.phonemes["L"],
        )
    if "S" in l.syll:
        print(
            l.phonemes["S"],
        )
    print(l.syll)
    ps = set()
    while len(ps) < 10:
        ps.add(l.name("city"))
    print(", ".join(ps))
    ps = set()
    while len(ps) < 10:
        ps.add(l.name("sea"))
    print(", ".join(ps))
    print("* * *")


if __name__ == "__main__":
    for _ in range(20):
        show_language(get_language())
