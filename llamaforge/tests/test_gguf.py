from llamaforge.core.gguf import guess_quantization

def test_quant():
    assert guess_quantization("Qwen-Q4_K_M.gguf") == "Q4_K_M"
    assert guess_quantization("foo-IQ3_XS.gguf") == "IQ3_XS"
