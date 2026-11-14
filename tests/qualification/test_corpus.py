import copy

import pytest

from tools.qualification.corpus import PROFILES, Profile, generate, validate
from tools.qualify_representative import main

pytest.importorskip('pyarrow')


@pytest.fixture
def corpus(tmp_path):
    profile = Profile('custom', 2, 500_000, 1_000_000, 20, (1,))
    return tmp_path / 'corpus', generate(tmp_path / 'corpus', profile, seed=7)


def test_deterministic_valid_parquet(corpus, tmp_path):
    path, manifest = corpus
    config = dict(manifest['profile'])
    config['clients'] = tuple(config['clients'])
    again = generate(tmp_path / 'again', Profile(**config), seed=7)
    assert manifest == again
    assert validate(path, manifest)['representative_qualified'] is False
    assert manifest['files'][0]['rows'] > 4096
    assert manifest['files'][0]['xxh3_128'] != manifest['files'][1]['xxh3_128']


@pytest.mark.parametrize('damage', ['count', 'size', 'rows', 'digest', 'identity', 'escape'])
def test_invalid_manifest_rejected(corpus, damage):
    path, original = corpus
    manifest = copy.deepcopy(original)
    entry = manifest['files'][0]
    if damage == 'count':
        manifest['files'].pop()
    elif damage == 'size':
        entry['bytes'] += 1
    elif damage == 'rows':
        entry['rows'] += 1
    elif damage == 'digest':
        entry['xxh3_128'] = '0' * 32
    elif damage == 'identity':
        entry['id'] = manifest['files'][1]['id']
    else:
        entry['name'] = '../secret.parquet'
    with pytest.raises(ValueError):
        validate(path, manifest)


def test_corrupt_bytes_rejected(corpus):
    path, manifest = corpus
    with (path / manifest['files'][0]['name']).open('r+b') as stream:
        stream.seek(100)
        stream.write(b'corruption')
    with pytest.raises(ValueError, match='integrity'):
        validate(path, manifest)


def test_existing_destination_not_overwritten(corpus):
    path, manifest = corpus
    before = (path / 'manifest.json').read_bytes()
    with pytest.raises(FileExistsError):
        generate(path, PROFILES['smoke'])
    assert (path / 'manifest.json').read_bytes() == before


def test_profiles_cannot_be_mislabeled():
    with pytest.raises(ValueError, match='custom'):
        Profile('representative', 4, 1_000_000, 4_000_000, 2000, (1, 2))


def test_smoke_never_claims_representative(tmp_path, capsys):
    assert main(['--output', str(tmp_path / 'smoke'), '--generate-only']) == 0
    assert '"representative_qualified": false' in capsys.readouterr().out


def test_overrides_require_custom(tmp_path):
    with pytest.raises(SystemExit):
        main(['--output', str(tmp_path / 'bad'), '--files', '2', '--generate-only'])


def test_generation_can_be_cancelled(tmp_path):
    def stop():
        raise TimeoutError('budget exhausted')
    with pytest.raises(TimeoutError):
        generate(tmp_path / 'stopped', PROFILES['smoke'], check=stop)
    assert not (tmp_path / 'stopped/manifest.json').exists()
