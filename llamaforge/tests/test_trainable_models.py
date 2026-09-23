from pathlib import Path

from llamaforge.core.trainable_models import TrainableModelManager


def test_trainable_checkpoint_assessment_prefers_transformers_weights():
    data={
        'id':'org/model', 'downloads':123, 'likes':4, 'pipeline_tag':'text-generation',
        'config':{'model_type':'gemma3'},
        'siblings':[
            {'rfilename':'config.json','size':1000},
            {'rfilename':'tokenizer.json','size':2000},
            {'rfilename':'model-00001-of-00002.safetensors','size':4_000_000_000},
            {'rfilename':'model-00002-of-00002.safetensors','size':4_000_000_000},
            {'rfilename':'model.safetensors.index.json','size':10000},
        ]
    }
    info=TrainableModelManager.assess_data(data)
    assert info.trainable is True
    assert info.has_safetensors is True
    assert info.has_config is True
    assert info.architecture == 'gemma3'
    assert info.weight_bytes == 8_000_000_000


def test_gguf_only_repo_is_not_marked_trainable():
    data={'id':'org/model-GGUF','siblings':[{'rfilename':'config.json','size':100},{'rfilename':'model-Q4_K_M.gguf','size':1234}]}
    info=TrainableModelManager.assess_data(data)
    assert info.trainable is False
    assert 'GGUF-only' in info.reason


def test_local_checkpoint_ready_requires_config_and_weights(tmp_path):
    assert not TrainableModelManager.local_checkpoint_ready(tmp_path)
    (tmp_path/'config.json').write_text('{}')
    assert not TrainableModelManager.local_checkpoint_ready(tmp_path)
    (tmp_path/'model.safetensors').write_bytes(b'x')
    assert TrainableModelManager.local_checkpoint_ready(tmp_path)


def test_peft_adapter_repo_is_trainable_and_prefers_lightweight_adapter_files():
    data={
        'id':'org/fine-tune',
        'config':{'model_type':'gemma3'},
        'siblings':[
            {'rfilename':'config.json','size':1000},
            {'rfilename':'tokenizer.json','size':2000},
            {'rfilename':'adapter_config.json','size':1000},
            {'rfilename':'adapter_model.safetensors','size':120_000_000},
            {'rfilename':'model-00001-of-00002.safetensors','size':8_000_000_000},
            {'rfilename':'model-00002-of-00002.safetensors','size':8_000_000_000},
        ]
    }
    info=TrainableModelManager.assess_data(data)
    assert info.trainable is True
    assert info.checkpoint_kind == 'peft_adapter'
    assert info.has_adapter_config is True
    files=TrainableModelManager._wanted_files(data)
    names={x['name'] for x in files}
    assert 'adapter_config.json' in names
    assert 'adapter_model.safetensors' in names
    assert 'model-00001-of-00002.safetensors' not in names


def test_local_peft_adapter_checkpoint_is_ready(tmp_path):
    (tmp_path/'adapter_config.json').write_text('{"base_model_name_or_path":"org/base"}', encoding='utf-8')
    (tmp_path/'adapter_model.safetensors').write_bytes(b'x')
    assert TrainableModelManager.local_checkpoint_ready(tmp_path)
    report=TrainableModelManager.verify_local(tmp_path)
    assert report['ok'] is True
    assert report['checkpoint_kind'] == 'peft_adapter'


def test_portable_training_models_root_is_one_level_above_app(tmp_path):
    from llamaforge.core.trainable_models import portable_training_models_root
    app = tmp_path / 'LlamaForge-0.15.1'
    app.mkdir()
    assert portable_training_models_root(app) == tmp_path / 'LlamaForgeModels'


def test_legacy_checkpoint_relocates_into_portable_root(tmp_path):
    legacy = tmp_path / 'legacy' / 'bases'
    old = legacy / 'org--model'
    old.mkdir(parents=True)
    (old / 'config.json').write_text('{}', encoding='utf-8')
    (old / 'model.safetensors').write_bytes(b'weights')
    (old / '.llamaforge-source.json').write_text('{"repo_id":"org/model","files":[]}', encoding='utf-8')
    portable = tmp_path / 'portable' / 'LlamaForgeModels'
    mgr = TrainableModelManager(portable)
    moved = Path(mgr.relocate_training_source(old, legacy_root=legacy))
    assert moved == portable / 'org--model'
    assert TrainableModelManager.local_checkpoint_ready(moved)
    assert not old.exists()


def test_verify_local_preserves_nested_manifest_paths(tmp_path):
    nested=tmp_path/'sub'; nested.mkdir()
    (tmp_path/'config.json').write_text('{}',encoding='utf-8')
    (nested/'model.safetensors').write_bytes(b'abc')
    (tmp_path/'.llamaforge-source.json').write_text(__import__('json').dumps({
        'repo_id':'org/model','files':[{'name':'sub/model.safetensors','size':3},{'name':'config.json','size':2}]
    }),encoding='utf-8')
    report=TrainableModelManager.verify_local(tmp_path)
    assert report['ok'] is True


def test_download_promotes_complete_part_without_redownloading(monkeypatch, tmp_path):
    import llamaforge.core.trainable_models as tm
    mgr=TrainableModelManager(tmp_path)
    data={
        'id':'org/model','sha':'deadbeef','config':{'model_type':'x'},
        'pipeline_tag':'text-generation','siblings':[{'rfilename':'config.json','size':2},{'rfilename':'tokenizer.json','size':2},{'rfilename':'model.safetensors','size':3}]
    }
    monkeypatch.setattr(mgr,'_model_data',lambda repo:data)
    dest=mgr.repo_local_dir('org/model'); dest.mkdir(parents=True)
    (dest/'config.json').write_bytes(b'{}')
    (dest/'tokenizer.json').write_bytes(b'{}')
    (dest/'model.safetensors.part').write_bytes(b'abc')
    def should_not_open(*a,**k):
        raise AssertionError('network should not be used for complete .part files')
    monkeypatch.setattr(tm,'open_url',should_not_open)
    out=Path(mgr.download_snapshot('org/model'))
    assert (out/'model.safetensors').read_bytes()==b'abc'
    assert not (out/'model.safetensors.part').exists()
    manifest=__import__('json').loads((out/'.llamaforge-source.json').read_text(encoding='utf-8'))
    assert manifest['revision']=='deadbeef'


def test_catalog_rejects_non_generative_checkpoint():
    data={
        'id':'org/bert-classifier','pipeline_tag':'text-classification',
        'config':{'model_type':'bert','architectures':['BertForSequenceClassification']},
        'siblings':[
            {'rfilename':'config.json','size':2},
            {'rfilename':'tokenizer.json','size':2},
            {'rfilename':'model.safetensors','size':3},
        ],
    }
    info=TrainableModelManager.assess_data(data)
    assert info.trainable is False
    assert 'Unsupported model task' in info.reason


def test_catalog_accepts_causal_checkpoint_with_tokenizer():
    data={
        'id':'org/causal','pipeline_tag':'text-generation',
        'config':{'model_type':'llama','architectures':['LlamaForCausalLM']},
        'siblings':[
            {'rfilename':'config.json','size':2},
            {'rfilename':'tokenizer.json','size':2},
            {'rfilename':'model.safetensors','size':3},
        ],
    }
    info=TrainableModelManager.assess_data(data)
    assert info.trainable is True
    assert info.checkpoint_kind == 'full_model'


def test_peft_catalog_size_counts_adapter_not_merged_weights():
    data={
        'id':'org/peft','pipeline_tag':'text-generation',
        'config':{'model_type':'llama','architectures':['LlamaForCausalLM']},
        'siblings':[
            {'rfilename':'adapter_config.json','size':1000},
            {'rfilename':'adapter_model.safetensors','size':120_000_000},
            {'rfilename':'model-00001-of-00002.safetensors','size':8_000_000_000},
            {'rfilename':'model-00002-of-00002.safetensors','size':8_000_000_000},
        ],
    }
    info=TrainableModelManager.assess_data(data)
    assert info.trainable is True
    assert info.checkpoint_kind == 'peft_adapter'
    assert info.weight_bytes == 120_000_000


def test_local_models_discovers_checkpoint_from_configured_parent(tmp_path):
    managed = tmp_path / 'managed'
    managed.mkdir()
    external_root = tmp_path / 'LlamaForgeModels'
    model = external_root / 'unsloth--gemma-3-4b-it-unsloth-bnb-4bit'
    model.mkdir(parents=True)
    (model / 'config.json').write_text('{"model_type":"gemma3"}', encoding='utf-8')
    (model / 'tokenizer.json').write_text('{}', encoding='utf-8')
    (model / 'model.safetensors').write_bytes(b'weights')
    (model / '.llamaforge-source.json').write_text(
        '{"repo_id":"unsloth/gemma-3-4b-it-unsloth-bnb-4bit"}', encoding='utf-8'
    )
    mgr = TrainableModelManager(managed)
    rows = mgr.local_models([str(external_root)])
    assert len(rows) == 1
    assert rows[0]['repo_id'] == 'unsloth/gemma-3-4b-it-unsloth-bnb-4bit'
    assert rows[0]['local_dir'] == str(model.resolve())
    assert rows[0]['has_safetensors'] is True
    assert rows[0]['architecture'] == 'gemma3'


def test_local_models_accepts_checkpoint_directory_itself(tmp_path):
    managed = tmp_path / 'managed'; managed.mkdir()
    model = tmp_path / 'direct-model'; model.mkdir()
    (model / 'config.json').write_text('{}', encoding='utf-8')
    (model / 'model.safetensors').write_bytes(b'x')
    rows = TrainableModelManager(managed).local_models([str(model)])
    assert [Path(x['local_dir']) for x in rows] == [model.resolve()]


def test_dequantized_repo_candidate_for_unsloth_bnb_checkpoint():
    mgr=TrainableModelManager(Path('.'))
    assert mgr.dequantized_repo_candidates('unsloth/gemma-3-4b-it-unsloth-bnb-4bit')[0] == 'unsloth/gemma-3-4b-it'
    assert mgr.is_prequantized_bnb_repo('unsloth/gemma-3-4b-it-unsloth-bnb-4bit') is True


def test_training_bundle_prefers_full_underlying_repo_over_prequantized_bnb(monkeypatch, tmp_path):
    mgr=TrainableModelManager(tmp_path/'models')
    source_repo='mshojaei77/gemma-3-4b-persian-v0'
    bnb_repo='unsloth/gemma-3-4b-it-unsloth-bnb-4bit'
    full_repo='unsloth/gemma-3-4b-it'

    def inspect(repo):
        if repo==source_repo:
            return {'repo_id':repo,'trainable':True,'checkpoint_kind':'peft_adapter','base_model':'','reason':''}
        if repo in {bnb_repo,full_repo}:
            return {'repo_id':repo,'trainable':True,'checkpoint_kind':'full_model','reason':''}
        raise AssertionError(repo)
    monkeypatch.setattr(mgr,'inspect',inspect)

    def fake_download(repo, **kwargs):
        d=mgr.repo_local_dir(repo); d.mkdir(parents=True,exist_ok=True)
        if repo==source_repo:
            (d/'adapter_config.json').write_text(__import__('json').dumps({'base_model_name_or_path':bnb_repo,'task_type':'CAUSAL_LM'}),encoding='utf-8')
            (d/'adapter_model.safetensors').write_bytes(b'a')
        else:
            (d/'config.json').write_text('{"model_type":"gemma3"}',encoding='utf-8')
            (d/'tokenizer.json').write_text('{}',encoding='utf-8')
            (d/'model.safetensors').write_bytes(b'w')
        return str(d)
    monkeypatch.setattr(mgr,'download_snapshot',fake_download)

    out=Path(mgr.download_training_bundle(source_repo))
    bundle=__import__('json').loads((out/'.llamaforge-bundle.json').read_text(encoding='utf-8'))
    assert bundle['declared_underlying_repo']==bnb_repo
    assert bundle['underlying_repo']==full_repo
    assert bundle['underlying_strategy']=='dynamic-4bit-from-full-checkpoint'
    assert Path(bundle['underlying_local_dir']) == mgr.repo_local_dir(full_repo)


def test_old_bundle_with_prequantized_dependency_requires_rebuild(tmp_path):
    root=tmp_path/'root'; mgr=TrainableModelManager(root)
    src=root/'source'; src.mkdir(parents=True)
    (src/'adapter_config.json').write_text('{"base_model_name_or_path":"unsloth/base-unsloth-bnb-4bit"}',encoding='utf-8')
    (src/'adapter_model.safetensors').write_bytes(b'a')
    under=root/'under'; under.mkdir()
    (under/'config.json').write_text(__import__('json').dumps({'quantization_config':{'quant_method':'bitsandbytes','load_in_4bit':True}}),encoding='utf-8')
    (under/'model.safetensors').write_bytes(b'w')
    (src/'.llamaforge-bundle.json').write_text(__import__('json').dumps({
        'source_repo':'org/source','underlying_repo':'unsloth/base-unsloth-bnb-4bit','underlying_local_dir':str(under)
    }),encoding='utf-8')
    info=mgr.training_bundle_info(src)
    assert info['ok'] is False
    assert info['needs_rebuild'] is True
