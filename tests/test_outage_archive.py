from datetime import datetime,timezone
import json
import sqlite3

import pandas as pd
import pytest

from scripts.import_outage_archive import normalise_archive,same_episode,nie_start,import_records


def source(**changes):
    row=dict(record_id='npg:123',source='Outage-Watch',operator='NPg',incident_ref='123',region=None,
        start_time_utc='2021-04-15T12:00:00Z',start_time_basis='reported',start_time_earliest_utc=None,
        reported_start_utc='2021-04-15T12:00:00Z',end_time_utc='2021-04-15T15:00:00Z',end_time_basis='last_snapshot_seen',
        duration_min=180,estimated_restoration_utc='2021-04-15T14:00:00Z',first_seen_utc='2021-04-15T12:05:00Z',
        last_seen_utc='2021-04-15T15:00:00Z',n_snapshots=3,lat=54.,lon=-2.,location_method='source_point',
        n_postcodes=1,n_postcodes_geocoded=1,postcodes='DL1 1AA',customers_affected=10,category='HV',
        cause='Wind',last_status=None,unplanned_basis='natureOfOutage')
    row.update(changes);return row


def test_archive_last_sighting_is_not_actual_restoration():
    row=normalise_archive(source())
    assert row['operator']=='NPG'
    assert row['restored_at_utc'] is None
    assert row['archive_duration_minutes']==180
    assert row['estimated_restoration_at_utc'].startswith('2021-04-15T14:00')
    assert not row['training_eligible'] and row['failed_asset_id'] is None
    assert 'actual_restoration_unknown' in json.loads(row['evidence_flags'])


def test_reported_restoration_and_invalid_date_order_are_distinguished():
    assert normalise_archive(source(end_time_basis='reported_restored'))['restored_at_utc'].startswith('2021-04-15T15:00')
    bad=normalise_archive(source(end_time_basis='reported_restored',end_time_utc='2021-04-15T11:00:00Z'))
    assert bad['restored_at_utc'] is None
    assert 'restoration_before_display_time' in json.loads(bad['evidence_flags'])


def test_reused_incident_ids_do_not_merge_unrelated_episodes():
    row=normalise_archive(source())
    other=dict(operator='NPG',source_event_key='123',reported_at_utc='2026-04-15T12:00:00Z')
    assert not same_episode(row,other)
    other['reported_at_utc']='2021-04-15T12:00:00Z'
    assert same_episode(row,other)
    other['operator']='UKPN'
    assert not same_episode(row,other)


def test_archive_observation_interval_only_matches_its_own_episode():
    row=normalise_archive(source(operator='WPD',start_time_basis='first_snapshot_seen',reported_start_utc=None))
    assert row['operator']=='NGED'
    assert same_episode(row,dict(operator='NGED',source_event_key='123',reported_at_utc='2021-04-15T13:00:00Z'))
    assert not same_episode(row,dict(operator='NGED',source_event_key='123',reported_at_utc='2021-04-16T13:00:00Z'))


def test_nie_year_inference_and_dst_uncertainty_are_preserved():
    collected=datetime(2026,10,25,tzinfo=timezone.utc)
    stamp,local=nie_start('1:32 PM, 24 Oct',collected)
    assert stamp.startswith('2026-10-24T12:32')
    assert nie_start('1:30 AM, 25 Oct',collected)[0] is None
    assert nie_start('not available',collected)==(None,None)
    assert nie_start('10:00 PM, 31 Dec',datetime(2026,1,1,tzinfo=timezone.utc))[0].startswith('2025-12-31')


def test_import_is_idempotent_and_keeps_alias_evidence(tmp_path):
    path=tmp_path/'outages.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE outages(event_key TEXT PRIMARY KEY,source_event_key TEXT,operator TEXT,reported_at_utc TEXT,source_kind TEXT)')
        db.execute("INSERT INTO outages VALUES ('official','123','NPG','2021-04-15T12:00:00Z','live_snapshot')")
        row=normalise_archive(source())
        stats,evidence=import_records(db,[row])
        assert stats['matched_existing_episode']==1
        assert evidence.iloc[0].canonical_event_key=='official'
        assert db.execute('SELECT COUNT(*) FROM outages').fetchone()[0]==1
        later=normalise_archive(source(record_id='npg:123:2',start_time_utc='2022-04-15T12:00:00Z',reported_start_utc='2022-04-15T12:00:00Z'))
        import_records(db,[later]);import_records(db,[later])
        assert db.execute('SELECT COUNT(*) FROM outages').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM outage_source_evidence').fetchone()[0]==2
