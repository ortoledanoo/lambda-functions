"""
Word dictionary for encoding/decoding codes.
Uses 1024 words (word0000 to word1023) to encode 100 bits as 10 words.
"""

DICTIONARY = [f"word{i:04d}" for i in range(1024)]


def decode_words_to_bits(words: list[str]) -> str:
    """
    Decode 10 words back to 100 bits.
    
    Args:
        words: List of 10 word strings
        
    Returns:
        String of 100 bits
    """
    if len(words) != 10:
        raise ValueError(f"Expected 10 words, got {len(words)}")
    
    bits = []
    for word in words:
        try:
            index = DICTIONARY.index(word)
        except ValueError:
            raise ValueError(f"Unknown word: {word}")
        bits.append(f"{index:010b}")
    return "".join(bits)

