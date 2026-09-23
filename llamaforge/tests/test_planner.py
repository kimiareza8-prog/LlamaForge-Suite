from llamaforge.core.hardware import HardwareInfo
from llamaforge.core.models import LocalModel
from llamaforge.core.planner import make_plan

def test_cpu_plan():
    hw=HardwareInfo("Linux","x","x86_64","Test CPU",16,8,16,12,[],[])
    m=LocalModel("/tmp/x.gguf","x",48,"Q4_K_M","qwen",8192,"")
    p=make_plan(m,hw,"Giant Model (Experimental)",True,4096)
    assert p.cpu_only
    assert p.gpu_layers == 0
    assert p.oversized
    assert p.load_mode == "mmap"


def test_performance_threads_leave_headroom():
    hw=HardwareInfo("Windows","11","AMD64","Test CPU",4,2,16,12,[],[])
    m=LocalModel("C:/x.gguf","x",5,"Q6_K","gemma4",8192,"")
    p=make_plan(m,hw,"Balanced",True,4096,thread_mode="performance")
    assert p.threads == 3
    assert p.threads_batch == 4

def test_manual_threads_have_guarded_oversubscription_limit():
    hw=HardwareInfo("Windows","11","AMD64","Test CPU",4,2,16,12,[],[])
    m=LocalModel("C:/x.gguf","x",5,"Q6_K","gemma4",8192,"")
    p=make_plan(m,hw,"Balanced",True,4096,thread_mode="manual",threads_override=99,threads_batch_override=99)
    assert p.threads == 8
    assert p.threads_batch == 8


def test_cpu_saturation_oversubscribes_and_sets_high_policy():
    hw=HardwareInfo("Windows","11","AMD64","Test CPU",4,2,16,12,[],[])
    m=LocalModel("C:/x.gguf","x",5,"Q6_K","gemma4",8192,"")
    p=make_plan(m,hw,"Balanced",True,4096,thread_mode="saturate",cpu_saturation=True)
    assert p.threads == 4 and p.threads_batch == 4
    assert p.cpu_saturation and p.cpu_strict
    assert p.cpu_priority == 2 and p.cpu_poll == 100
    assert p.cpu_affinity_count == 4


def test_cpu_target_percent_maps_to_logical_threads():
    hw=HardwareInfo("Windows","11","AMD64","Test CPU",4,2,16,12,[],[])
    m=LocalModel("C:/x.gguf","x",5,"Q6_K","gemma4",8192,"")
    p50=make_plan(m,hw,"Balanced",True,4096,thread_mode="target",cpu_target_percent=50)
    p75=make_plan(m,hw,"Balanced",True,4096,thread_mode="target",cpu_target_percent=75)
    p100=make_plan(m,hw,"Balanced",True,4096,thread_mode="target",cpu_target_percent=100)
    assert (p50.threads,p50.threads_batch)==(2,2)
    assert (p75.threads,p75.threads_batch)==(3,3)
    assert (p100.threads,p100.threads_batch)==(4,4)
    assert p100.cpu_poll == 100
    assert p100.cpu_priority == 1


def test_ram_only_uses_full_ram_without_mmap_and_disables_lazy_loading():
    hw=HardwareInfo("Windows","11","AMD64","Test CPU",8,4,16,14,[],[])
    m=LocalModel("C:/small.gguf","small",4,"Q4_K_M","qwen",8192,"")
    p=make_plan(m,hw,"Balanced",True,4096,memory_mode="ram_only")
    assert p.memory_mode == "ram_only"
    assert p.load_mode == "none"
    assert p.lazy_mode == "off"
    assert not p.no_warmup
    assert not p.oversized


def test_ssd_test_uses_mmap_lazy_and_skips_warmup():
    hw=HardwareInfo("Windows","11","AMD64","Test CPU",8,4,16,10,[],[])
    m=LocalModel("C:/large.gguf","large",15,"Q4_K_M","qwen",8192,"")
    p=make_plan(m,hw,"Giant Model (Experimental)",True,4096,memory_mode="ssd_test")
    assert p.memory_mode == "ssd_test"
    assert p.load_mode == "mmap"
    assert p.lazy_mode == "on"
    assert p.no_warmup
    assert p.prompt_cache_mb == 0


def test_hybrid_uses_full_ram_when_model_fits_safe_budget():
    hw=HardwareInfo("Windows","11","AMD64","Test CPU",8,4,16,14,[],[])
    m=LocalModel("C:/medium.gguf","medium",6,"Q4_K_M","qwen",8192,"")
    p=make_plan(m,hw,"Balanced",True,4096,memory_mode="hybrid")
    assert p.memory_mode == "hybrid"
    assert p.load_mode == "none"
    assert p.lazy_mode == "off"
    assert not p.no_warmup


def test_hybrid_keeps_mmap_for_oversized_model():
    hw=HardwareInfo("Windows","11","AMD64","Test CPU",8,4,16,10,[],[])
    m=LocalModel("C:/large.gguf","large",15,"Q4_K_M","qwen",8192,"")
    p=make_plan(m,hw,"Giant Model (Experimental)",True,4096,memory_mode="hybrid")
    assert p.memory_mode == "hybrid"
    assert p.load_mode == "mmap"
    assert p.oversized
