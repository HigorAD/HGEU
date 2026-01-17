
# -*- coding: utf-8 -*-
"""
convert.py — Conversão de planilha (CSV/Excel) de horários para o modelo normalizado
Uso:
    python convert.py caminho/arquivo.csv -o saida/
    python convert.py caminho/arquivo.xlsx -o saida/
"""
import os, argparse, pandas as pd, json, re, unicodedata

DIAS_MAP = {'Seg':'Segunda','Segunda':'Segunda','Ter':'Terça','Terça':'Terça','Terca':'Terça',
            'Qua':'Quarta','Quarta':'Quarta','Qui':'Quinta','Quinta':'Quinta','Sex':'Sexta','Sexta':'Sexta'}

def normalize_day(d):
    if pd.isna(d): return None
    return DIAS_MAP.get(str(d).strip(), str(d).strip())

def normalize_turno(t):
    if pd.isna(t): return None
    t = str(t).strip()
    return '1º' if t in ['1','1º','1o','1ª'] else ('2º' if t in ['2','2º','2o','2ª'] else ('Pré' if t in ['pre','Pre','Pré','pré'] else (t or None)))

def slug_id(text):
    if pd.isna(text): return None
    s = unicodedata.normalize('NFKD', str(text))
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r'[^a-zA-Z0-9]+','_',s).strip('_').lower()

def curso_from_turma(t):
    t = str(t).strip()
    return 'CC' if t.startswith('CC') else ('SI' if t.startswith('SI') else 'OUTRO')

def read_input(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.csv':
        df = pd.read_csv(path, dtype=str, keep_default_na=False, comment="#", skip_blank_lines=True)
    elif ext in ('.xlsx','.xlsm'):
        df = pd.read_excel(path, dtype=str, engine='openpyxl')
    elif ext == '.xls':
        df = pd.read_excel(path, dtype=str, engine='xlrd')
    else:
        raise ValueError(f'Extensão não suportada: {ext}')
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in ['Turma','Código','Nome da Disciplina','Professor','DiaSemana','Turno'] if c not in df.columns]
    if missing:
        raise ValueError(f'Colunas obrigatórias ausentes: {missing}')
    return df

def process_df(df):
    df = df.copy()
    mask_header = df['Turma'].str.strip().eq('Turma')
    mask_empty = (df[['Turma','Código','Nome da Disciplina','Professor','DiaSemana','Turno']]
                   .apply(lambda r: ''.join(map(str,r)), axis=1).str.strip()=='')
    df = df[~mask_header & ~mask_empty]

    for c in df.columns: df[c] = df[c].astype(str).str.strip()
    df['DiaSemana'] = df['DiaSemana'].apply(normalize_day)
    df['Turno'] = df['Turno'].apply(normalize_turno)
    df['Tipo'] = df['Tipo'].str.upper().replace({'': None}) if 'Tipo' in df.columns else None

    for c in ['CH','TEO']:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')

    df['Turma_list'] = df['Turma'].apply(lambda t: [p.strip() for p in str(t).split('/')])

    all_turmas = sorted({t for sub in df['Turma_list'] for t in sub if t})
    turmas_dim = pd.DataFrame({'turma_id': all_turmas})
    turmas_dim['turma_nome'] = turmas_dim['turma_id']
    turmas_dim['curso_id'] = turmas_dim['turma_id'].apply(curso_from_turma)

    cursos_map = {'CC':'Ciência da Computação','SI':'Sistemas de Informação','OUTRO':'Curso Desconhecido'}
    cursos_dim = (turmas_dim[['curso_id']].drop_duplicates()
                  .assign(curso_nome=lambda x: x['curso_id'].map(cursos_map)))

    disciplinas_dim = (df[['Código','Nome da Disciplina','CH','TEO']]
                       .rename(columns={'Código':'disc_id','Nome da Disciplina':'nome_disciplina'})
                       .groupby('disc_id', as_index=False)
                       .agg({'nome_disciplina':'first','CH':'max','TEO':'max'}))
    disciplinas_dim['codigo'] = disciplinas_dim['disc_id']
    disciplinas_dim = disciplinas_dim[['disc_id','codigo','nome_disciplina','CH','TEO']]

    prof_dim = (df[['Professor']].dropna().query("Professor != ''").drop_duplicates())
    prof_dim['prof_id'] = prof_dim['Professor'].apply(slug_id)
    if prof_dim['prof_id'].duplicated().any():
        counts = {}; ids = []
        for pid in prof_dim['prof_id']:
            counts[pid] = counts.get(pid, 0) + 1
            ids.append(pid if counts[pid]==1 else f'{pid}_{counts[pid]}')
        prof_dim['prof_id'] = ids
    prof_dim = prof_dim[['prof_id','Professor']].rename(columns={'Professor':'nome_professor'})

    valid = df.copy()
    valid = valid[valid['DiaSemana'].notna() & (valid['DiaSemana']!='')]
    valid = valid[valid['Turno'].notna() & (valid['Turno']!='')]
    valid = valid[valid['Professor'].notna() & (valid['Professor']!='')]

    rows = []
    for _, r in valid.iterrows():
        for turma in r['Turma_list']:
            turma = turma.strip()
            if not turma: continue
            rows.append({
                'turma_id': turma,
                'disc_id': r['Código'],
                'prof_id': slug_id(r['Professor']),
                'dia_semana': normalize_day(r['DiaSemana']),
                'turno': normalize_turno(r['Turno']),
                'tipo': (r['Tipo'] if r.get('Tipo') in ['T','P', 'EAD'] else None)
            })
    agenda_fact = pd.DataFrame(rows).merge(prof_dim[['prof_id']], on='prof_id', how='left')

    tempo_dim = pd.DataFrame([
        {'dia_id':'SEG-0','dia_semana':'Segunda','ordem_dia':1,'turno_id':'0','turno_nome':'Pré','ordem_turno':0},
        {'dia_id':'SEG-1','dia_semana':'Segunda','ordem_dia':1,'turno_id':'1','turno_nome':'1º','ordem_turno':1},
        {'dia_id':'SEG-2','dia_semana':'Segunda','ordem_dia':1,'turno_id':'2','turno_nome':'2º','ordem_turno':2},

        {'dia_id':'TER-0','dia_semana':'Terça','ordem_dia':2,'turno_id':'0','turno_nome':'Pré','ordem_turno':0},
        {'dia_id':'TER-1','dia_semana':'Terça','ordem_dia':2,'turno_id':'1','turno_nome':'1º','ordem_turno':1},
        {'dia_id':'TER-2','dia_semana':'Terça','ordem_dia':2,'turno_id':'2','turno_nome':'2º','ordem_turno':2},

        {'dia_id':'QUA-0','dia_semana':'Quarta','ordem_dia':3,'turno_id':'0','turno_nome':'Pré','ordem_turno':0},
        {'dia_id':'QUA-1','dia_semana':'Quarta','ordem_dia':3,'turno_id':'1','turno_nome':'1º','ordem_turno':1},
        {'dia_id':'QUA-2','dia_semana':'Quarta','ordem_dia':3,'turno_id':'2','turno_nome':'2º','ordem_turno':2},

        {'dia_id':'QUI-0','dia_semana':'Quinta','ordem_dia':4,'turno_id':'0','turno_nome':'Pré','ordem_turno':0},
        {'dia_id':'QUI-1','dia_semana':'Quinta','ordem_dia':4,'turno_id':'1','turno_nome':'1º','ordem_turno':1},
        {'dia_id':'QUI-2','dia_semana':'Quinta','ordem_dia':4,'turno_id':'2','turno_nome':'2º','ordem_turno':2},

        {'dia_id':'SEX-0','dia_semana':'Sexta','ordem_dia':5,'turno_id':'0','turno_nome':'Pré','ordem_turno':0},
        {'dia_id':'SEX-1','dia_semana':'Sexta','ordem_dia':5,'turno_id':'1','turno_nome':'1º','ordem_turno':1},
        {'dia_id':'SEX-2','dia_semana':'Sexta','ordem_dia':5,'turno_id':'2','turno_nome':'2º','ordem_turno':2},
    ])

    skip_mask = ~(df.index.isin(valid.index))
    skipped = df[skip_mask][['Turma','Código','Nome da Disciplina','Professor','DiaSemana','Turno','Tipo']]
    conflicts = (agenda_fact.groupby(['turma_id','dia_semana','turno']).size()
                 .reset_index(name='n').query('n>1'))
    prof_conflicts = (agenda_fact.groupby(['prof_id','dia_semana','turno']).size()
                      .reset_index(name='n').query('n>1'))

    report = {
        'input_rows': int(len(df)),
        'agenda_rows': int(len(agenda_fact)),
        'skipped_rows': int(len(skipped)),
        'skipped_sample': skipped.head(15).to_dict(orient='records'),
        'conflicts': conflicts.to_dict(orient='records'),
        'prof_conflicts': prof_conflicts.to_dict(orient='records'),
    }

    return {
        'cursos': cursos_dim, 'turmas': turmas_dim, 'disciplinas': disciplinas_dim,
        'professores': prof_dim, 'tempo': tempo_dim, 'agenda': agenda_fact
    }, report

def save_outputs(out_dir, dfs, report):
    os.makedirs(out_dir, exist_ok=True)
    for name, df in dfs.items():
        df.to_csv(os.path.join(out_dir, f'{name}.csv'), index=False)
    with open(os.path.join(out_dir, 'conversion_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description='Converter planilha de horários em CSVs para o app.')
    parser.add_argument('input', help='Arquivo de entrada (.csv, .xlsx, .xls)')
    parser.add_argument('-o','--out', default='horarios_out', help='Pasta de saída')
    args = parser.parse_args()

    df = read_input(args.input)
    dfs, report = process_df(df)
    save_outputs(args.out, dfs, report)
    print(f"Arquivos gerados em: {args.out}")
    # ✅ Use aspas duplas fora para não conflitar com as aspas simples dos índices:
    print(
        f"Linhas de entrada: {report['input_rows']} | "
        f"Linhas em agenda: {report['agenda_rows']} | "
        f"Ignoradas: {report['skipped_rows']}"
    )

if __name__ == '__main__':
    main()
