import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from src.clients.pubmed_client import (
    PubMedClient,
    PubMedClientError,
    PubMedRateLimitError,
)
from src.data_fetcher import DataFetcher
from src.pubmed_url_collector import PubMedURLCollector


@pytest.fixture
def mock_pubmed_client():
    """Return a mock PubMedClient."""
    client = MagicMock(spec=PubMedClient)
    client.get_abstract_by_id = AsyncMock()
    client.get_abstracts_by_ids = AsyncMock()
    return client


@pytest.fixture
def mock_pubmed_abstract() -> Dict[str, Any]:
    """Return a mock abstract."""
    return {
        "id": "15858239",
        "title": "The role of ret gene in the pathogenesis of Hirschsprung disease",
        "abstract": "This is a test abstract about Hirschsprung disease.",
        "authors": ["Smigiel R", "Patkowski D", "Slezak R"],
        "publication_date": "2004 Jul-Sep",
        "journal": "Med Wieku Rozwoj",
        "doi": "10.1000/test.12345",
        "keywords": ["Hirschsprung Disease", "Genetics", "RET Gene"],
    }


@pytest.fixture
def mock_url_collector():
    """Return a mock PubMedURLCollector."""
    collector = MagicMock(spec=PubMedURLCollector)
    collector.collect_urls.return_value = {
        "http://www.ncbi.nlm.nih.gov/pubmed/15858239",
        "http://www.ncbi.nlm.nih.gov/pubmed/12345678",
        "http://www.ncbi.nlm.nih.gov/pubmed/87654321",
    }
    return collector


@pytest.fixture
def data_fetcher(mock_pubmed_client, tmp_path):
    """Return a DataFetcher with a mock PubMedClient and temporary data directory."""
    with patch("src.data_fetcher.PubMedURLCollector") as mock_collector_cls:
        # Configure the mock collector
        mock_collector = mock_collector_cls.return_value
        mock_collector.collect_urls.return_value = {
            "http://www.ncbi.nlm.nih.gov/pubmed/15858239",
            "http://www.ncbi.nlm.nih.gov/pubmed/12345678",
            "http://www.ncbi.nlm.nih.gov/pubmed/87654321",
        }

        # Create data fetcher with the mock and temp directory
        fetcher = DataFetcher(
            mock_pubmed_client,
            data_dir=str(tmp_path),
            batch_size=2,  # Small batch size for testing
            rate_limit_per_sec=10,  # High rate limit to speed up tests
            max_retries=2,
            concurrent_requests=2,  # Testing with 2 concurrent requests
        )

        return fetcher


@pytest.mark.asyncio
async def test_extract_pubmed_id(data_fetcher):
    """Test extracting PubMed ID from URL."""
    url = "http://www.ncbi.nlm.nih.gov/pubmed/15858239"
    pubmed_id = data_fetcher._extract_pubmed_id(url)
    assert pubmed_id == "15858239"


@pytest.mark.asyncio
async def test_fetch_single_abstract_success(
    data_fetcher, mock_pubmed_client, mock_pubmed_abstract
):
    """Test successful fetching of a single abstract."""
    # Configure the mock client
    mock_pubmed_client.get_abstract_by_id.return_value = mock_pubmed_abstract

    # Call the method
    url = "http://www.ncbi.nlm.nih.gov/pubmed/15858239"

    # Patch open to avoid file operations
    with patch("builtins.open", mock_open()) as mock_file:
        with patch("json.dump") as mock_json_dump:
            result = await data_fetcher.fetch_single_abstract(url)

    # Verify the result
    assert result is not None
    assert result["id"] == "15858239"
    assert (
        result["title"]
        == "The role of ret gene in the pathogenesis of Hirschsprung disease"
    )

    # Verify the client was called with the correct ID
    mock_pubmed_client.get_abstract_by_id.assert_called_once_with("15858239")

    # Verify the file was written
    mock_file.assert_called()
    mock_json_dump.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_single_abstract_already_exists(
    data_fetcher, mock_pubmed_client, mock_pubmed_abstract
):
    """Test fetching an abstract that already exists."""
    # Configure Path.exists to return True
    with patch.object(Path, "exists", return_value=True):
        # Mock open to return the abstract
        with patch(
            "builtins.open", mock_open(read_data=json.dumps(mock_pubmed_abstract))
        ):
            with patch("json.load", return_value=mock_pubmed_abstract):
                result = await data_fetcher.fetch_single_abstract(
                    "http://www.ncbi.nlm.nih.gov/pubmed/15858239"
                )

    # Verify the result is from the file
    assert result is not None
    assert result["id"] == "15858239"

    # Client should not be called
    mock_pubmed_client.get_abstract_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_single_abstract_rate_limit(data_fetcher, mock_pubmed_client):
    """Test rate limit handling during single abstract fetching."""
    # Configure the mock client to raise rate limit error first, then succeed
    mock_pubmed_client.get_abstract_by_id.side_effect = [
        PubMedRateLimitError("Rate limit exceeded", status_code=429),
        mock_pubmed_abstract,  # Second call succeeds
    ]

    # Call the method
    url = "http://www.ncbi.nlm.nih.gov/pubmed/15858239"

    # Patch sleep to avoid waiting in tests
    with patch("asyncio.sleep", return_value=None) as mock_sleep:
        with patch("builtins.open", mock_open()) as mock_file:
            with patch("json.dump"):
                result = await data_fetcher.fetch_single_abstract(url)

    # Verify the result is successful after retry
    assert result is not None
    assert result == mock_pubmed_abstract

    # Verify sleep was called for rate limit
    assert mock_sleep.call_count >= 1

    # Verify multiple calls to the client
    assert mock_pubmed_client.get_abstract_by_id.call_count == 2


@pytest.mark.asyncio
async def test_fetch_single_abstract_error(data_fetcher, mock_pubmed_client):
    """Test error handling during single abstract fetching."""
    # Configure the mock client to raise an exception
    mock_pubmed_client.get_abstract_by_id.side_effect = PubMedClientError("Test error")

    # Call the method
    url = "http://www.ncbi.nlm.nih.gov/pubmed/15858239"

    result = await data_fetcher.fetch_single_abstract(url)

    # Verify None result due to error
    assert result is None

    # Verify the client was called
    mock_pubmed_client.get_abstract_by_id.assert_called_with("15858239")


@pytest.mark.asyncio
async def test_fetch_batch(data_fetcher, mock_pubmed_client, mock_pubmed_abstract):
    """Test fetching a batch of abstracts."""
    # This test is no longer needed as we've removed batch processing with process_batch
    pass


@pytest.mark.asyncio
async def test_fetch_all_abstracts(
    data_fetcher, mock_pubmed_client, mock_pubmed_abstract
):
    """Test fetching all abstracts concurrently."""
    # Setup abstracts with different IDs
    abstract1 = mock_pubmed_abstract
    abstract2 = {**mock_pubmed_abstract, "id": "12345678"}
    abstract3 = {**mock_pubmed_abstract, "id": "87654321"}

    # Configure mock to return different abstracts for different IDs
    def side_effect(pubmed_id):
        if pubmed_id == "15858239":
            return abstract1
        elif pubmed_id == "12345678":
            return abstract2
        elif pubmed_id == "87654321":
            return abstract3
        return None

    mock_pubmed_client.get_abstract_by_id.side_effect = side_effect

    # Set of URLs
    urls = {
        "http://www.ncbi.nlm.nih.gov/pubmed/15858239",
        "http://www.ncbi.nlm.nih.gov/pubmed/12345678",
        "http://www.ncbi.nlm.nih.gov/pubmed/87654321",
    }

    # Mock the fetch_single_abstract method to control testing of fetch_all_abstracts
    with patch.object(
        data_fetcher, "fetch_single_abstract", new_callable=AsyncMock
    ) as mock_fetch_single:
        # Configure mock to return abstracts based on URL
        mock_fetch_single.side_effect = lambda url: side_effect(
            data_fetcher._extract_pubmed_id(url)
        )

        # Mock sleep to avoid waiting
        with patch("asyncio.sleep") as mock_sleep:
            results = await data_fetcher.fetch_all_abstracts(urls)

    # Verify results
    assert len(results) == 3
    ids = [r["id"] for r in results]
    assert "15858239" in ids
    assert "12345678" in ids
    assert "87654321" in ids

    # Verify fetch_single_abstract was called for each URL
    assert mock_fetch_single.call_count == 3


@pytest.mark.asyncio
async def test_run_success(data_fetcher, mock_pubmed_client, mock_pubmed_abstract):
    """Test successful run method."""
    # Mock fetch_all_abstracts to control test flow
    with patch.object(
        data_fetcher, "fetch_all_abstracts", new_callable=AsyncMock
    ) as mock_fetch_all:
        # Configure mock to return a list of abstracts
        mock_fetch_all.return_value = [
            mock_pubmed_abstract,
            {**mock_pubmed_abstract, "id": "12345678"},
            {**mock_pubmed_abstract, "id": "87654321"},
        ]

        # Mock file operations
        with patch("builtins.open", mock_open()) as mock_file:
            with patch("json.dump") as mock_json_dump:
                result = await data_fetcher.run()

    # Verify result is a summary dict
    assert result is not None
    assert "total_urls" in result
    assert "successful_fetches" in result
    assert result["successful_fetches"] == 3
    assert result["failed_fetches"] == 0  # All abstracts were fetched

    # Verify fetch_all_abstracts was called
    mock_fetch_all.assert_called_once()


@pytest.mark.asyncio
async def test_run_with_errors(data_fetcher, mock_pubmed_client):
    """Test run method with failed fetches."""
    # Mock fetch_all_abstracts to return a partial list (simulating some failures)
    with patch.object(
        data_fetcher, "fetch_all_abstracts", new_callable=AsyncMock
    ) as mock_fetch_all:
        # Return only 1 abstract even though we have 3 URLs
        mock_fetch_all.return_value = [
            {"id": "15858239", "title": "Test Abstract", "abstract": "Test"}
        ]

        # Mock file operations
        with patch("builtins.open", mock_open()) as mock_file:
            with patch("json.dump") as mock_json_dump:
                result = await data_fetcher.run()

    # Verify result shows some failed fetches
    assert result is not None
    assert result["total_urls"] == 3
    assert result["successful_fetches"] == 1
    assert result["failed_fetches"] == 2  # 2 out of 3 failed


@pytest.mark.asyncio
async def test_run_no_urls(data_fetcher):
    """Test run method when no URLs are found."""
    # Configure URL collector to return empty set
    data_fetcher.url_collector.collect_urls.return_value = set()

    # Run with no URLs
    with patch("builtins.print") as mock_print:
        result = await data_fetcher.run()

    # Verify None result due to no URLs
    assert result is None


@pytest.mark.asyncio
async def test_process_batch(data_fetcher, mock_pubmed_client, mock_pubmed_abstract):
    """Test removed process_batch method - this test is no longer needed."""
    pass
