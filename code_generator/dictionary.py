"""
Word dictionary for encoding/decoding codes.
Uses 1024 words (word0000 to word1023) to encode 100 bits as 10 words.
"""

DICTIONARY = [f"word{i:04d}" for i in range(1024)]


def encode_bits_to_words(bits_100: str) -> list[str]:
    """
    Encode 100 bits as 10 words (10 bits per word).
    
    Args:
        bits_100: String of exactly 100 bits
        
    Returns:
        List of 10 word strings
    """
    if len(bits_100) != 100:
        raise ValueError(f"Expected 100 bits, got {len(bits_100)}")
    
    words = []
    for i in range(0, 100, 10):
        slice_10 = bits_100[i:i + 10]
        index = int(slice_10, 2)
        words.append(DICTIONARY[index])
    return words


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

