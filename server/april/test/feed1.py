# Load Data
from llama_index.core import Settings, VectorStoreIndex, SimpleDirectoryReader
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.vllm import VllmServer
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.core import StorageContext
from llama_index.core import Settings
import qdrant_client


#TODO: for each source,, we need a data source class that will load the data from the source
#For example CodeSource, WikiSource, etc
documents = SimpleDirectoryReader("data/paul_graham/", recursive=True).load_data()

client = qdrant_client.QdrantClient(
    url = "http://localhost:6333",
)
Settings.embed_model = HuggingFaceEmbedding(model_name="WhereIsAI/UAE-Large-V1", device="cpu")
vector_store = QdrantVectorStore(client=client, collection_name="paul_graham")
storage_context = StorageContext.from_defaults(vector_store=vector_store)
index = VectorStoreIndex.from_documents(
    documents,
    storage_context=storage_context,
    show_progress=True,
)

