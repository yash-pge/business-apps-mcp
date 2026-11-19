import logging
import os

import requests

logger = logging.getLogger(__name__)

def search_business_apps_vectordb(
    kb_id: str,
    access_token: str,
    query: str,
    number_of_results: int = 5,
    rerank: bool = False,
    metadata_filter: dict | None = None,
) -> list[dict]:
    """Searches the Business Apps vector database through the retrieve API.
    The tool calls the retrieve API over HTTPS; its input and output deliberately mirror the shape of Amazon Bedrock's `bedrock-agent-runtime.retrieve()` operation:
    retrievalQuery, retrievalConfiguration, retrievalResults are based on https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agent-runtime/client/retrieve.html

        Args:
            kb_id: The knowledge base ID to search.
            access_token: The Entra ID access token to use for authentication.
            query: The search query.
            number_of_results: The number of results to return.
            rerank: Whether to use the reranker.
            metadata_filter: A dictionary of metadata filters to apply to the search.
        Returns:
            A list of retrieval result objects (Bedrock retrievalResults shape).
    """

    retrieval_query = {'text': query}

    retrieval_configuration = {
        'vectorSearchConfiguration': {
            'numberOfResults': number_of_results,
        }
    }

    if rerank:
        retrieval_configuration["vectorSearchConfiguration"]["rerankingConfiguration"] = {
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                'modelConfiguration': {
                    'modelArn': os.getenv("RERANKING_MODEL_ARN")
                }
            }
        }

    if metadata_filter:
        # Ensure filter exists
        retrieval_configuration['vectorSearchConfiguration'].setdefault('filter', {})
        retrieval_configuration['vectorSearchConfiguration']['filter'].update(metadata_filter)

    payload = {
        "knowledgeBaseId": kb_id,
        "retrievalQuery": retrieval_query,
        "retrievalConfiguration": retrieval_configuration,
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        API_REQUEST_TIMEOUT = int(os.getenv("API_REQUEST_TIMEOUT", "30"))

        response = requests.post(
            os.getenv('API_ENDPOINT'), json=payload, headers=headers, timeout=API_REQUEST_TIMEOUT
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as e:
        logger.warning("search_business_apps_vectordb: API timed out after %ss", API_REQUEST_TIMEOUT)
        raise RuntimeError(f"search timed out after {API_REQUEST_TIMEOUT}s.") from e
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        logger.warning("search_business_apps_vectordb: API returned HTTP %s", status)
        # Don't leak the raw error body back to the caller; give it a usable summary.
        raise RuntimeError(f"search failed with HTTP {status}.") from e
    except requests.exceptions.RequestException as e:
        logger.exception("search_business_apps_vectordb: request to API failed")
        raise RuntimeError("Could not reach the search service.") from e

    try:
        data = response.json()
    except ValueError as e:
        logger.exception("search_business_apps_vectordb: API returned non-JSON response")
        raise RuntimeError("search returned a malformed response.") from e

    results = data.get("retrievalResults", [])
    logger.info("search_business_apps_vectordb: API returned %d result(s)", len(results))

    return results


if __name__ == "__main__":
    # FOR TESTING
    logging.basicConfig(level=logging.INFO)

    import argparse

    parser = argparse.ArgumentParser(description="Call Business Apps search API with access token.")
    parser.add_argument(
        "--access-token",
        required=True,
        help="Bearer access token to authenticate the search API request.",
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Query to send to the search API request.",
    )
    args = parser.parse_args()

    kb_id = os.getenv("KB_ID")
    search_business_apps_vectordb(
        kb_id=kb_id,
        access_token=args.access_token,
        query=args.query,
        number_of_results=10,
        )
