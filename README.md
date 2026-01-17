# Pacote inicial de horários (Python/Streamlit)

## Estrutura
- `cursos.csv`, `turmas.csv`, `disciplinas.csv`, `professores.csv`, `tempo.csv`, `agenda.csv`
- `app.py` — app Streamlit com 3 páginas: Grade por Turma, Agenda do Professor, Conflitos.

## Como executar
```bash
pip install streamlit pandas
cd horarios_package
streamlit run app.py
```

## Padrões
- Dias: Segunda, Terça, Quarta, Quinta, Sexta
- Turnos: 1º, 2º
- `agenda.csv`: uma linha por aula (turma + disciplina + professor + dia + turno + tipo)

## Próximos passos
- Adicionar horários reais (inicio/fim) na dimensão `tempo.csv`.
- Incluir validações de conflitos de professor por dia/turno.
- Criar página de filtro por curso.
