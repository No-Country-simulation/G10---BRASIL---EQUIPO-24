"""
storage.py: ETAPA 6 do MediFlow (OCI Object Storage)

O QUE É O OBJECT STORAGE?
Um "HD na nuvem" para guardar arquivos. Você cria um BUCKET (como uma pasta raiz)
e dentro dele salva OBJETOS. Não existem pastas de verdade: o "caminho" é só o nome
do objeto, e a barra "/" é uma convenção (chamamos de PREFIXO). Nosso bucket:

    mediflow-documentos-clinicos/
      recebidos/DOC-1.pdf                      <- original, assim que chega
      processados/urgentes/DOC-2.json          <- decisão de casos urgentes
      processados/farmacia_hospitalar/DOC-3.json
      auditoria_humana/DOC-4.json              <- aguardando um auditor
      processados/revisados/DOC-4.json         <- depois da revisão humana

TRÊS "BACKENDS" (mesma interface, destinos diferentes):
  - ArmazenamentoOCI       : usa o SDK `oci` e o bucket de verdade (Always Free).
  - ArmazenamentoSeaweedFS : usa o `boto3` (cliente S3) no gateway S3 do SeaweedFS,
                             ótimo para rodar "a nuvem" no seu próprio computador.
                             Serve também para qualquer servidor compatível com S3.
  - ArmazenamentoLocal     : grava numa pasta do seu computador, imitando o bucket.
                             Serve para desenvolver e testar sem nenhum servidor.

`criar_armazenamento()` escolhe sozinho (variável STORAGE_BACKEND: auto|oci|seaweedfs|local).

COMANDOS ÚTEIS:
    python storage.py --criar-bucket   # cria o bucket (OCI ou SeaweedFS; 1 vez só)
    python storage.py --testar         # grava, lê, lista e apaga um objeto de teste
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

BUCKET_PADRAO = "mediflow-documentos-clinicos"


class ErroArmazenamento(Exception):
    """Falha ao gravar/ler no armazenamento."""


# ---------------------------------------------------------------------------
# Interface comum: o resto do projeto só usa estes métodos
# ---------------------------------------------------------------------------
class ArmazenamentoBase:
    bucket: str = BUCKET_PADRAO
    status_ok: str = "sucesso"  # valor de `status_backup` quando a gravação dá certo

    # --- 4 operações básicas (cada backend implementa) ---
    def salvar(self, caminho: str, dados: bytes, content_type: str = "application/octet-stream"):
        raise NotImplementedError

    def ler(self, caminho: str) -> bytes:
        raise NotImplementedError

    def listar(self, prefixo: str = "") -> list[str]:
        raise NotImplementedError

    def apagar(self, caminho: str) -> None:
        raise NotImplementedError

    # --- conveniências construídas em cima das básicas ---
    def salvar_json(self, caminho: str, objeto: dict) -> None:
        corpo = json.dumps(objeto, ensure_ascii=False, indent=2).encode("utf-8")
        self.salvar(caminho, corpo, "application/json")

    def ler_json(self, caminho: str) -> dict:
        return json.loads(self.ler(caminho).decode("utf-8"))

    def mover(self, origem: str, destino: str) -> None:
        """OCI não tem 'mover': copiamos para o destino e só então apagamos a origem."""
        self.salvar(destino, self.ler(origem))
        self.apagar(origem)


def _validar_caminho(caminho: str) -> str:
    """Bloqueia caminhos perigosos como '../segredo' ou '/etc/passwd'."""
    partes = caminho.split("/")
    if not caminho or caminho.startswith("/") or ".." in partes or "\\" in caminho:
        raise ErroArmazenamento(f"Caminho de objeto inválido: {caminho!r}")
    return caminho


# ---------------------------------------------------------------------------
# Backend local (desenvolvimento / testes)
# ---------------------------------------------------------------------------
class ArmazenamentoLocal(ArmazenamentoBase):
    status_ok = "sucesso_local"  # honestidade: NÃO foi para a nuvem

    def __init__(self, raiz: str = None, bucket: str = BUCKET_PADRAO):
        self.bucket = bucket
        self.raiz = Path(raiz or os.getenv("ARMAZENAMENTO_LOCAL_DIR", "armazenamento_local")) / bucket

    def _arquivo(self, caminho: str) -> Path:
        arquivo = (self.raiz / _validar_caminho(caminho)).resolve()
        if self.raiz.resolve() not in arquivo.parents:
            raise ErroArmazenamento(f"Caminho fora do bucket: {caminho!r}")
        return arquivo

    def salvar(self, caminho, dados, content_type="application/octet-stream"):
        arquivo = self._arquivo(caminho)
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_bytes(dados)

    def ler(self, caminho):
        try:
            return self._arquivo(caminho).read_bytes()
        except FileNotFoundError as e:
            raise ErroArmazenamento(f"Objeto não encontrado: {caminho}") from e

    def listar(self, prefixo=""):
        if not self.raiz.exists():
            return []
        todos = (str(p.relative_to(self.raiz)).replace(os.sep, "/") for p in self.raiz.rglob("*") if p.is_file())
        return sorted(n for n in todos if n.startswith(prefixo))

    def apagar(self, caminho):
        self._arquivo(caminho).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Backend OCI (nuvem de verdade)
# ---------------------------------------------------------------------------
class ArmazenamentoOCI(ArmazenamentoBase):
    def __init__(self, client=None, namespace: str = None, bucket: str = None):
        """`client` e `namespace` podem ser injetados (é como os testes funcionam)."""
        self.bucket = bucket or os.getenv("OCI_BUCKET", BUCKET_PADRAO)
        if client is None:
            import oci  # import aqui: quem usa só o backend local não precisa do SDK

            arquivo = os.path.expanduser(os.getenv("OCI_CONFIG_FILE", "~/.oci/config"))
            config = oci.config.from_file(file_location=arquivo, profile_name=os.getenv("OCI_PROFILE", "DEFAULT"))
            client = oci.object_storage.ObjectStorageClient(config)
            self.config = config
        self.client = client
        # Namespace = identificador único da sua conta no Object Storage.
        self.namespace = namespace or os.getenv("OCI_NAMESPACE") or client.get_namespace().data

    def salvar(self, caminho, dados, content_type="application/octet-stream"):
        self.client.put_object(
            self.namespace, self.bucket, _validar_caminho(caminho), dados, content_type=content_type
        )

    def ler(self, caminho):
        return self.client.get_object(self.namespace, self.bucket, _validar_caminho(caminho)).data.content

    def listar(self, prefixo=""):
        nomes, inicio = [], None
        while True:  # a OCI devolve no máximo 1000 por vez: seguimos a paginação
            resp = self.client.list_objects(self.namespace, self.bucket, prefix=prefixo, start=inicio)
            nomes += [o.name for o in resp.data.objects]
            inicio = resp.data.next_start_with
            if not inicio:
                return nomes

    def apagar(self, caminho):
        self.client.delete_object(self.namespace, self.bucket, _validar_caminho(caminho))

    def criar_bucket(self) -> None:
        """Cria o bucket (camada Standard) no compartimento raiz da conta."""
        import oci

        detalhes = oci.object_storage.models.CreateBucketDetails(
            name=self.bucket, compartment_id=self.config["tenancy"]
        )
        self.client.create_bucket(self.namespace, detalhes)


# ---------------------------------------------------------------------------
# Backend SeaweedFS (gateway S3)
# O SeaweedFS fala o protocolo S3, então usamos o cliente padrão `boto3`.
# Para subir um SeaweedFS local com Docker (master + volume + filer + S3 num só contêiner):
#   docker run -d --name seaweedfs -p 9333:9333 -p 8888:8888 -p 8333:8333 \
#     -v seaweed_data:/data chrislusf/seaweedfs server -dir=/data -s3
# O gateway S3 fica em http://localhost:8333 (sem autenticação, se nenhuma identidade
# for configurada; o boto3 só exige que as chaves existam, qualquer valor serve).
# ---------------------------------------------------------------------------
def _codigo_s3(erro: Exception) -> str:
    """Código de erro S3 de uma exceção do boto3 (ex.: 'NoSuchKey', '404')."""
    return str(getattr(erro, "response", {}).get("Error", {}).get("Code", ""))


class ArmazenamentoSeaweedFS(ArmazenamentoBase):
    status_ok = "sucesso_seaweedfs"  # honestidade: foi para o SeaweedFS, não para a OCI

    def __init__(self, client=None, bucket: str = None):
        """`client` pode ser injetado (é como os testes funcionam)."""
        self.bucket = bucket or os.getenv("SEAWEEDFS_BUCKET", BUCKET_PADRAO)
        if client is None:
            import boto3  # import aqui: quem não usa SeaweedFS não precisa do SDK
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=os.getenv("SEAWEEDFS_ENDPOINT", "http://localhost:8333"),
                aws_access_key_id=os.getenv("SEAWEEDFS_ACCESS_KEY", "mediflow"),
                aws_secret_access_key=os.getenv("SEAWEEDFS_SECRET_KEY", "mediflow-secret"),
                region_name=os.getenv("SEAWEEDFS_REGION", "us-east-1"),
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},  # http://host:8333/bucket/objeto
                    retries={"max_attempts": 3},
                    connect_timeout=5,
                    read_timeout=30,
                ),
            )
        self.client = client
        self._bucket_ok = False

    def criar_bucket(self) -> None:
        """Cria o bucket se ainda não existir."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception as e:
            if _codigo_s3(e) not in ("404", "NoSuchBucket", "NotFound"):
                raise
            try:
                self.client.create_bucket(Bucket=self.bucket)
            except Exception as e2:  # outro processo pode ter criado no meio tempo
                if _codigo_s3(e2) not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    raise
        self._bucket_ok = True

    def salvar(self, caminho, dados, content_type="application/octet-stream"):
        if not self._bucket_ok:  # cria o bucket sob demanda, na primeira gravação
            self.criar_bucket()
        self.client.put_object(
            Bucket=self.bucket, Key=_validar_caminho(caminho), Body=dados, ContentType=content_type
        )

    def ler(self, caminho):
        try:
            resposta = self.client.get_object(Bucket=self.bucket, Key=_validar_caminho(caminho))
        except Exception as e:
            if _codigo_s3(e) in ("NoSuchKey", "NoSuchBucket", "404"):
                raise ErroArmazenamento(f"Objeto não encontrado: {caminho}") from e
            raise
        return resposta["Body"].read()

    def listar(self, prefixo=""):
        nomes = []
        try:
            # list_objects_v2 devolve no máximo 1000 por vez: o paginator segue as páginas.
            for pagina in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefixo):
                nomes += [obj["Key"] for obj in pagina.get("Contents", [])]
        except Exception as e:
            if _codigo_s3(e) in ("NoSuchBucket", "404"):
                return []  # bucket ainda não criado = nada guardado
            raise
        return sorted(nomes)

    def apagar(self, caminho):
        self.client.delete_object(Bucket=self.bucket, Key=_validar_caminho(caminho))


# ---------------------------------------------------------------------------
# Escolha automática do backend
# ---------------------------------------------------------------------------
def criar_armazenamento() -> ArmazenamentoBase:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    modo = os.getenv("STORAGE_BACKEND", "auto").lower()
    config_oci = os.path.expanduser(os.getenv("OCI_CONFIG_FILE", "~/.oci/config"))

    if modo == "local":
        return ArmazenamentoLocal()
    if modo == "oci":
        return ArmazenamentoOCI()
    if modo == "seaweedfs":
        return ArmazenamentoSeaweedFS()
    # auto: OCI se estiver configurada; senão SeaweedFS se SEAWEEDFS_ENDPOINT foi definido; senão local
    if os.getenv("OCI_NAMESPACE") and os.path.exists(config_oci):
        return ArmazenamentoOCI()
    if os.getenv("SEAWEEDFS_ENDPOINT"):
        return ArmazenamentoSeaweedFS()
    return ArmazenamentoLocal()


# ---------------------------------------------------------------------------
# Linha de comando
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    comando = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        armazenamento = criar_armazenamento()
        print(f"Backend: {type(armazenamento).__name__} | bucket: {armazenamento.bucket}")
        if comando == "--criar-bucket":
            if not isinstance(armazenamento, (ArmazenamentoOCI, ArmazenamentoSeaweedFS)):
                sys.exit("Backend local não tem bucket para criar. Configure a OCI ou o SeaweedFS (veja o README).")
            armazenamento.criar_bucket()
            print("Bucket criado com sucesso.")
        elif comando == "--testar":
            armazenamento.salvar_json("recebidos/_teste.json", {"ok": True})
            print("Gravou:", armazenamento.listar("recebidos/_teste"))
            print("Leu:   ", armazenamento.ler_json("recebidos/_teste.json"))
            armazenamento.apagar("recebidos/_teste.json")
            print("Apagou. Teste concluído com sucesso.")
        else:
            print("Uso: python storage.py --criar-bucket | --testar")
    except Exception as erro:
        sys.exit(f"ERRO: {erro}")
