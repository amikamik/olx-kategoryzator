#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Uruchamia 3 testy równolegle:
- 1x deterministyczny (temp=0.0)
- 2x niedeterministyczny (temp=0.7)
"""

import subprocess
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

def run_test(test_name, temperature, output_dir):
    """Uruchamia pojedynczy test"""
    print(f"\n[{test_name}] START (temp={temperature})")
    
    cmd = [
        'python', 
        'test_deepseek.py',
        '--temperature', str(temperature),
        '--output-dir', output_dir
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        if result.returncode == 0:
            print(f"[{test_name}] SUKCES")
            return {
                'test': test_name,
                'status': 'OK',
                'output_dir': output_dir,
                'temperature': temperature
            }
        else:
            print(f"[{test_name}] BLAD: {result.stderr[:200]}")
            return {
                'test': test_name,
                'status': 'ERROR',
                'error': result.stderr
            }
    except Exception as e:
        print(f"[{test_name}] WYJATEK: {e}")
        return {
            'test': test_name,
            'status': 'EXCEPTION',
            'error': str(e)
        }

def compare_results(dir1, dir2):
    """Porównuje wyniki dwóch testów"""
    file1 = os.path.join(dir1, 'podsumowanie.json')
    file2 = os.path.join(dir2, 'podsumowanie.json')
    
    if not os.path.exists(file1) or not os.path.exists(file2):
        return {"error": "Brak plików podsumowania"}
    
    with open(file1, 'r', encoding='utf-8') as f:
        data1 = json.load(f)
    with open(file2, 'r', encoding='utf-8') as f:
        data2 = json.load(f)
    
    ids1 = set(data1['niepewne_ids'])
    ids2 = set(data2['niepewne_ids'])
    
    return {
        'ids_test1': ids1,
        'ids_test2': ids2,
        'wspolne': ids1 & ids2,
        'tylko_test1': ids1 - ids2,
        'tylko_test2': ids2 - ids1,
        'zgodnosc': len(ids1 & ids2) / max(len(ids1 | ids2), 1) * 100
    }

def main():
    print("="*70)
    print("RÓWNOLEGŁE TESTY WALIDACJI - 4x NONDET_RUN2")
    print("="*70)
    
    # Definicja testów - 4x TEN SAM (najlepszy nondet_run2: temp=0.7)
    tests = [
        ('NONDET_run2_copy1', 0.7, 'wyniki_nondet_run2_copy1'),
        ('NONDET_run2_copy2', 0.7, 'wyniki_nondet_run2_copy2'),
        ('NONDET_run2_copy3', 0.7, 'wyniki_nondet_run2_copy3'),
        ('NONDET_run2_copy4', 0.7, 'wyniki_nondet_run2_copy4')
    ]
    
    print(f"\nUruchamiam 4x identyczny test RÓWNOLEGLE...")
    print("Parametry KAŻDEGO: temp=0.7, batch-size=30, max-rundy=5")
    print("Cel: niedeterminizm znajdzie różne podzbiory problemów")
    print("Szacowany czas: ~3-5 minut\n")
    
    # Uruchom równolegle
    wyniki = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(run_test, name, temp, out): name
            for name, temp, out in tests
        }
        
        for future in as_completed(futures):
            test_name = futures[future]
            try:
                result = future.result()
                wyniki.append(result)
            except Exception as e:
                print(f"[{test_name}] WYJATEK: {e}")
    
    # Podsumowanie
    print("\n" + "="*70)
    print("PODSUMOWANIE TESTÓW")
    print("="*70)
    
    for w in wyniki:
        status_emoji = "OK" if w['status'] == 'OK' else "ERROR"
        print(f"{status_emoji} {w['test']}: {w['status']}")
    
    # Porównania
    print("\n" + "="*70)
    print("PORÓWNANIA WYNIKÓW - 4 IDENTYCZNE TESTY")
    print("="*70)
    
    # Zbierz wszystkie niepewne IDs
    all_ids = set()
    test_results = {}
    
    for i in range(1, 5):
        path = f'wyniki_nondet_run2_copy{i}/podsumowanie.json'
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                ids = set(data['niepewne_ids'])
                test_results[f'copy{i}'] = ids
                all_ids.update(ids)
                print(f"\nCopy {i}: {len(ids)} niepewnych")
    
    print(f"\n{'='*70}")
    print(f"UNIA WSZYSTKICH 4 TESTÓW: {len(all_ids)} unikalnych niepewnych")
    print(f"IDs: {sorted(all_ids, key=int)}")
    
    # Pokrycie każdego testu
    print(f"\n{'='*70}")
    print("POKRYCIE (ile z unii znalazł każdy test):")
    for name, ids in test_results.items():
        coverage = len(ids) / len(all_ids) * 100 if all_ids else 0
        print(f"  {name}: {len(ids)}/{len(all_ids)} = {coverage:.1f}%")
    
    # Wspólne dla wszystkich
    if len(test_results) == 4:
        wspolne_wszystkie = test_results['copy1'] & test_results['copy2'] & test_results['copy3'] & test_results['copy4']
        print(f"\nWspólne dla WSZYSTKICH 4 testów: {len(wspolne_wszystkie)}")
        if wspolne_wszystkie:
            print(f"  IDs: {sorted(wspolne_wszystkie, key=int)}")
    
    print("\n" + "="*70)
    print("FOLDERY Z WYNIKAMI:")
    print("  - wyniki_nondet_run2_copy1/")
    print("  - wyniki_nondet_run2_copy2/")
    print("  - wyniki_nondet_run2_copy3/")
    print("  - wyniki_nondet_run2_copy4/")
    print("="*70)

if __name__ == "__main__":
    main()
