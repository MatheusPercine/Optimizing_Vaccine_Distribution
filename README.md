# Aplicacao Gurobi Python

## Como rodar os modelos
1. No arquivo `caso_nacional/execucao.csv`, escolha um dos modelos possiveis (1, 2) e desative/ative a penalidade com False/True.
2. Execute o script: `python aplicacao_modelos.py`.

## Sobre
Este codigo gera cenarios de demanda e oferta de vacinas, calcula custos/tempos de transporte e resolve modelos de otimizacao (com ou sem intermediacao estadual) para distribuir doses entre municipios e postos, registrando os resultados em CSVs de saida.
