# Verificação e distribuição

Os testes locais e a CI usam o mesmo núcleo instalado. O orçamento conta testes lógicos de geometria; não equivale a um cronômetro ou medidor de memória. A implementação e as unidades da contagem estão em [API.md](API.md).

## Executar os testes

```sh
python -m pip install . -r requirements-build.txt
python -m unittest discover -s tests -v
python -m build
python scripts/check_distribution.py --dist dist
```

`check_distribution.py` confere uma lista fechada do wheel, o entry point, ausência de dependências de execução, licença MIT, cada hash/tamanho RECORD e igualdade com os bytes da fonte. O sdist é confrontado com a lista pública de fontes e seus metadados gerados, sem incluir ambientes virtuais, dados locais ou contexto de trabalho.

## Instalar fora da árvore de fontes

```sh
python scripts/check_distribution.py --install dist/zonas_geojson-1.0.0-py3-none-any.whl
python scripts/check_distribution.py --install dist/zonas_geojson-1.0.0.tar.gz
```

O comando cria um ambiente temporário independente, instala o artefato, confirma a origem da importação, executa o exemplo e o leitor independente do ZIP e verifica que repetir a saída preserva o arquivo existente. A instalação do sdist prepara o backend de build fixado; o wheel pode ser instalado sem dependências. Nenhum ambiente do usuário é usado como destino.

## Consumidores externos

[INTEROPERABILIDADE.md](INTEROPERABILIDADE.md) separa execuções reais, arquivos derivados e conferência de evidência armazenada. Os consumidores são ferramentas opcionais de QA; não integram o runtime. A comparação com GEOS é limitada aos cenários numéricos escolhidos. Os casos que excedem a resolução de float têm esperados racionais literais separados.

A [CI](https://github.com/martinlofranodeoliveira/zonas-geojson/actions/workflows/ci.yml) testa Python 3.11–3.14 em Linux, 3.14 em Windows e um trabalho separado com GEOS/GDAL em Linux. O histórico de cada execução identifica seu commit; a configuração por si só não prova que uma execução terminou.

## Release

A versão reúne wheel, sdist, `exemplo-zonas.zip` e `SHA256SUMS`. Os arquivos do ZIP do exemplo têm manifesto próprio. A conferência de entrega compara os assets públicos baixados novamente com os preparados, verifica os formatos e confronta a fonte pública com os arquivos Git por SHA. Consulte o commit e os checks associados à versão publicada.

## Evidência local desta versão

Em 9 de novembro de 2026 passaram 59 testes: 24 de geometria/modelo, 14 de JSON/relatórios, seis da CLI, nove do leitor independente e seis da distribuição. O wheel tem 15 membros, incluindo nove arquivos de runtime; o sdist contém 35 arquivos públicos e sete metadados gerados. Ambos foram instalados em ambientes temporários fora do checkout, executaram o exemplo de 13 pontos e preservaram o destino em uma segunda tentativa. O exemplo tem oito arquivos e passou pela leitura independente.

Uma revisão encontrou que a comparação de fontes usava igualdade de Python e confundia booleanos com números. A comparação agora percorre os tipos JSON; o passe dirigido reproduziu a recusa `REPORT_SOURCE` e marcou esse achado como resolvido. Isso não equivale a uma certificação geral de todos os casos.

O [registro do consumidor](../fixtures/interop/execucao-2026-11-09.json) documenta uma execução real sem rede, nove hashes de runtime conferidos, 3.667 pares por GEOS, 736 pontos por GDAL/GeoPackage/SQLite e três casos decimais por ouro literal. Os dados são fictícios. A figura de exemplo foi renderizada por FFmpeg/librsvg e inspecionada; sua leitura gráfica não determina limites territoriais reais.

Para preparar os quatro assets após os testes e um commit limpo, com o pacote instalado e os dois artefatos construídos:

```sh
python scripts/prepare_release.py --dist dist
```

A preparação confere os bytes de runtime importados contra a fonte, wheel e sdist, gera um exemplo novo pela CLI, chama o leitor independente e escreve SHA256SUMS. Não sobrescreve um exemplo ou checksums preexistentes.
