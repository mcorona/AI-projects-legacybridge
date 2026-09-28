# ADR-001: Cascada de costo local → OmniRoute → Bedrock

## Contexto
El proyecto debe demostrar calidad de nivel producción con presupuesto casi nulo, y a la vez
alinearse con AWS Certified Generative AI Developer – Professional (Bedrock).

## Decisión
- Qwen3.6-35B-A3B en LM Studio es el proveedor por defecto (costo $0, datos no salen de la máquina).
- OmniRoute (gateway OpenAI-compatible, `localhost:20128/v1`) es respaldo gratuito, **solo con datos sintéticos**.
- Amazon Bedrock (Claude Haiku 4.5 vía inference profile) para escalamiento y la corrida de evaluación final.
- Toda la lógica de selección vive en `llm/router.py` y `config/models.yaml`.

## Consecuencias
- (+) Costo de desarrollo cercano a cero; la comparación de costo/calidad se vuelve un resultado publicable.
- (+) Cambiar de proveedor no toca el agente.
- (−) Los modelos gratuitos de OmniRoute cambian con el tiempo: no se usan para cifras publicadas.
- (−) Funciones exclusivas de Bedrock (Guardrails, prompt caching) quedan como capas opcionales.
