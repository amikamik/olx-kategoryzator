#!/usr/bin/env python3
"""
Usuwa po 1 sztuce z każdej pary PRAWDZIWYCH duplikatów:
- ten sam tytuł
- różne ID

Strategia:
- zostawiamy nowsze ogłoszenie (wyższe ID)
- usuwamy starsze (niższe ID)
- jeśli status='active', najpierw command=deactivate, potem DELETE
"""

import os
import sys
import time
import requests
from collections import defaultdict

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(PROJECT_DIR, 'config', 'tokeny_olx_2026-01-20.txt')
BASE_URL = 'https://www.olx.pl/api/partner'


def load_token() -> str:
    if not os.path.exists(TOKEN_FILE):
        return ''
    with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('access_token='):
                return line.strip().split('=', 1)[1]
    return ''


def headers(token: str):
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Version': '2.0',
    }


def fetch_all_ads(token: str):
    all_ads = []
    offset = 0
    limit = 50
    while True:
        r = requests.get(f'{BASE_URL}/adverts', headers=headers(token), params={'offset': offset, 'limit': limit}, timeout=30)
        if r.status_code != 200:
            print(f'❌ Błąd pobierania: {r.status_code} {r.text}')
            sys.exit(1)
        data = r.json().get('data', [])
        if not data:
            break
        all_ads.extend(data)
        if len(data) < limit:
            break
        offset += limit
    return all_ads


def build_true_duplicates(raw_ads):
    unique_by_id = {}
    for ad in raw_ads:
        ad_id = str(ad.get('id'))
        if ad_id not in unique_by_id:
            unique_by_id[ad_id] = ad

    by_title = defaultdict(list)
    for ad in unique_by_id.values():
        # Tylko aktywne/limited — pomijamy removed_by_user, outdated itp.
        if ad.get('status') not in ('active', 'limited'):
            continue
        title = (ad.get('title') or '').strip()
        price = 0.0
        try:
            price = float((ad.get('price') or {}).get('value') or 0)
        except (TypeError, ValueError):
            pass
        by_title[title].append({
            'id': str(ad.get('id')),
            'status': ad.get('status') or '',
            'price': price,
            'title': title,
        })

    true_dups = {t: items for t, items in by_title.items() if len(items) > 1}

    # dla każdej grupy zostawiamy najwyższe ID, usuwamy pozostałe
    to_delete = []
    for title, items in true_dups.items():
        sorted_items = sorted(items, key=lambda x: int(x['id']), reverse=True)
        keep = sorted_items[0]
        for it in sorted_items[1:]:
            to_delete.append({
                'title': title,
                'delete': it,
                'keep': keep,
            })

    return true_dups, to_delete


def deactivate_if_active(token: str, advert_id: str, status: str):
    if status != 'active':
        return True
    payload = {'command': 'deactivate', 'is_success': True}
    r = requests.post(f'{BASE_URL}/adverts/{advert_id}/commands', headers=headers(token), json=payload, timeout=30)
    if r.status_code == 204:
        return True
    # jeśli nie jest już active, też może być ok do kasowania
    if r.status_code in (400, 404):
        return True
    print(f'   ❌ Deactivate failed ID {advert_id}: {r.status_code} {r.text[:200]}')
    return False


def delete_advert(token: str, advert_id: str):
    r = requests.delete(f'{BASE_URL}/adverts/{advert_id}', headers=headers(token), timeout=30)
    if r.status_code in (200, 204, 404):
        return True
    print(f'   ❌ Delete failed ID {advert_id}: {r.status_code} {r.text[:200]}')
    return False


def main():
    token = load_token()
    if not token:
        print('❌ Brak access_token')
        sys.exit(1)

    ads = fetch_all_ads(token)
    true_dups, to_delete = build_true_duplicates(ads)

    print('=' * 72)
    print('USUWANIE PRAWDZIWYCH DUPLIKATÓW (po 1 sztuce z pary)')
    print('=' * 72)
    print(f'Grupy prawdziwych duplikatów: {len(true_dups)}')
    print(f'Zaplanowane do usunięcia: {len(to_delete)}')

    if not to_delete:
        print('✅ Brak ogłoszeń do usunięcia')
        return

    ok = 0
    err = 0
    for i, item in enumerate(to_delete, 1):
        d = item['delete']
        k = item['keep']
        print(f"\n[{i}/{len(to_delete)}] {item['title']}")
        print(f"   USUŃ: ID {d['id']} | {d['price']:.2f} PLN | {d['status']}")
        print(f"   ZOSTAW: ID {k['id']} | {k['price']:.2f} PLN | {k['status']}")

        if not deactivate_if_active(token, d['id'], d['status']):
            err += 1
            continue

        time.sleep(0.35)
        if delete_advert(token, d['id']):
            ok += 1
            print('   ✅ Usunięto')
        else:
            err += 1

        time.sleep(0.35)

    print('\n' + '=' * 72)
    print(f'Wynik: usunięto {ok}, błędy {err}')


if __name__ == '__main__':
    main()
