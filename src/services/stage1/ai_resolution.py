"""Stage 1 AI Resolution Service.

Implements AI-powered industry and brand resolution using GPT-4o-mini.
No static mapping tables - all resolution happens at runtime via AI.

AI Calls (from design doc):
- #1: Industry resolution (always fires)
- #2: Brand resolution (always fires)
- #3: Web enrichment (fallback only)
- #4: Proxy candidate scoring (fallback only)
- #5: Competitor suggestion (always fires) - NEW
- #6: Produktmarke filtering (per brand) - NEW

Web Search:
- Uses OpenAI function calling (Tools API)
- Max 2 web searches per request
- Queries in German for German market data
"""

import json
import asyncio
import httpx
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

from openai import AsyncOpenAI

from src.config import get_settings


class MatchType(Enum):
    EXACT = "exact"
    SIMILARITY = "similarity"
    NOT_FOUND = "not_found"


@dataclass
class IndustryResolutionResult:
    """Result of AI Call #1: Industry Resolution."""
    yougov_sectors: List[str]
    nielsen_sectors: List[str]
    confidence: float = 1.0
    reasoning: Optional[str] = None


@dataclass
class BrandResolutionResult:
    """Result of AI Call #2: Brand Resolution."""
    yougov_brand: Optional[str]
    nielsen_brand: Optional[str]
    exact: bool
    match_type: MatchType
    confidence: float = 1.0
    reasoning: Optional[str] = None


@dataclass
class WebEnrichmentResult:
    """Result of AI Call #3: Web Enrichment."""
    size_tier: str  # small, mid, large, enterprise
    revenue_range: Optional[str]  # EUR range
    market_position: str  # market_leader, challenger, niche
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProxyCandidate:
    """A proxy candidate brand with similarity score."""
    brand_label: str
    score: float
    reasoning: str
    size_tier_match: float
    revenue_match: float
    position_match: float
    kpi_proximity: float


@dataclass
class ProxyScoringResult:
    """Result of AI Call #4: Proxy Candidate Scoring."""
    candidates: List[ProxyCandidate]
    threshold: float = 0.85


@dataclass
class CompetitorSuggestionResult:
    """Result of AI Call #5: Competitor Suggestion."""
    competitors: List[str]  # brand_label values
    reasoning: str
    confidence: float = 1.0


@dataclass
class ProduktmarkeFilterResult:
    """Result of AI Call #6: Produktmarke Filtering."""
    relevant: List[str]      # Include in analysis
    excluded: List[str]      # Exclude from analysis
    reasoning: Dict[str, str]  # Produktmarke -> reason
    confidence: float = 1.0


class AIResolutionService:
    """Service for AI-powered industry and brand resolution.

    Uses GPT-4o-mini for fast, cost-effective resolution.
    Handles semantic equivalences (e.g., "Food II" = "Food III").
    """

    MODEL = "gpt-4o-mini"
    MODEL_FALLBACK = "gpt-4o"  # For complex cases

    # Web search tool definition for OpenAI function calling
    WEB_SEARCH_TOOL = {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for company information, market data, revenue, and market position. Use German queries for German market data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query (preferably in German for German market)"
                    }
                },
                "required": ["query"]
            }
        }
    }

    def __init__(self, api_key: Optional[str] = None):
        settings = get_settings()
        self.client = AsyncOpenAI(api_key=api_key or settings.openai_api_key)
        self._web_search_provider = None  # Set via configure_web_search()

    def configure_web_search(self, provider: str, api_key: Optional[str] = None):
        """Configure web search provider.

        Args:
            provider: "tavily", "serpapi", or "duckduckgo"
            api_key: API key for the provider (not needed for duckduckgo)
        """
        self._web_search_provider = provider
        self._web_search_api_key = api_key

    # =========================================================================
    # AI CALL #1: Industry Resolution
    # =========================================================================

    async def resolve_industry(
        self,
        user_industry: str,
        yougov_sectors: List[str],
        nielsen_sectors: List[str],
        debug_logger: Optional[Any] = None,
    ) -> IndustryResolutionResult:
        """AI Call #1: Map user's industry input to database sector values.

        Model: GPT-4o-mini
        Est. time: ~200ms

        The AI handles semantic equivalences (e.g., "Food II" and "Food III"
        may both apply). No hardcoded rules needed.
        """
        system_prompt = """You are a German media planning data analyst.
Map the user's industry input to the best matching values from the provided YouGov and Nielsen sector lists.

Rules:
1. ALWAYS include ALL semantically related sectors, not just exact matches
2. "Lebensmittel" (German for food) should match ALL food sectors: "Food I", "Food II", "Food III", "Lebensmittel I", etc.
3. If user specifies "Lebensmittel I", still include "Food II" and other food-related sectors
4. Handle German/English equivalences: Lebensmittel=Food, Getränke=Beverages, etc.
5. When in doubt, be INCLUSIVE - include more sectors rather than fewer
6. If no good match exists, return empty lists

Return ONLY valid JSON in this format:
{"yougov_sectors": ["sector1", "sector2"], "nielsen_sectors": ["sector1"], "confidence": 0.95, "reasoning": "brief explanation"}"""

        user_prompt = f"""User input: "{user_industry}"

YouGov sectors: {json.dumps(yougov_sectors[:100], ensure_ascii=False)}

Nielsen sectors: {json.dumps(nielsen_sectors[:100], ensure_ascii=False)}

Map the user's industry to matching sectors from both lists."""

        # Save prompt for debugging
        if debug_logger:
            debug_logger.save_y1_prompt(system_prompt, user_prompt)

        try:
            response = await self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )

            result = json.loads(response.choices[0].message.content)

            return IndustryResolutionResult(
                yougov_sectors=result.get("yougov_sectors", []),
                nielsen_sectors=result.get("nielsen_sectors", []),
                confidence=result.get("confidence", 1.0),
                reasoning=result.get("reasoning"),
            )

        except Exception as e:
            # Return empty result on error
            return IndustryResolutionResult(
                yougov_sectors=[],
                nielsen_sectors=[],
                confidence=0.0,
                reasoning=f"Error: {str(e)}",
            )

    # =========================================================================
    # AI CALL #2: Brand Resolution
    # =========================================================================

    async def resolve_brand(
        self,
        user_brand: str,
        yougov_brands: List[str],
        nielsen_brands: List[str],
    ) -> BrandResolutionResult:
        """AI Call #2: Match user's brand to database brand values.

        Model: GPT-4o-mini
        Est. time: ~200ms

        Handles:
        - GmbH/AG suffixes
        - Umlauts (ä, ö, ü)
        - Abbreviations
        - Case differences
        """
        system_prompt = """You are a German media planning data analyst.
Match the user's brand name to the closest entries in the provided YouGov and Nielsen brand lists.

Rules:
1. Handle GmbH/AG suffixes (e.g., "Nike" matches "Nike GmbH")
2. Handle umlauts (e.g., "Mueller" matches "Müller")
3. Handle abbreviations and case differences
4. Match based on meaning, not exact string
5. Set "exact" to true only if you're confident this is the same brand

Return ONLY valid JSON in this format:
{"yougov_brand": "matched_brand" or null, "nielsen_brand": "MATCHED_BRAND" or null, "exact": true/false, "confidence": 0.95, "reasoning": "brief explanation"}"""

        user_prompt = f"""User brand: "{user_brand}"

YouGov brands: {json.dumps(yougov_brands[:200], ensure_ascii=False)}

Nielsen brands: {json.dumps(nielsen_brands[:200], ensure_ascii=False)}

Find the matching brand in both lists."""

        try:
            response = await self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )

            result = json.loads(response.choices[0].message.content)

            exact = result.get("exact", False)
            match_type = MatchType.EXACT if exact else (
                MatchType.SIMILARITY if result.get("yougov_brand") else MatchType.NOT_FOUND
            )

            return BrandResolutionResult(
                yougov_brand=result.get("yougov_brand"),
                nielsen_brand=result.get("nielsen_brand"),
                exact=exact,
                match_type=match_type,
                confidence=result.get("confidence", 0.0),
                reasoning=result.get("reasoning"),
            )

        except Exception as e:
            return BrandResolutionResult(
                yougov_brand=None,
                nielsen_brand=None,
                exact=False,
                match_type=MatchType.NOT_FOUND,
                confidence=0.0,
                reasoning=f"Error: {str(e)}",
            )

    # =========================================================================
    # AI CALL #3: Web Enrichment (Fallback)
    # =========================================================================

    async def enrich_brand_from_web(
        self,
        brand: str,
        industry: str,
    ) -> WebEnrichmentResult:
        """AI Call #3: Enrich unknown brand with web search data.

        Only fires when brand is NOT found in YouGov (Step 2 returns exact=false).

        Performs max 2 web searches:
        1. "{brand} Deutschland Umsatz Unternehmensgröße"
        2. "{brand} {industry} Deutschland Marktposition"

        Extracts:
        - Size tier: small / mid / large / enterprise
        - Revenue range (EUR)
        - Market position: market_leader / challenger / niche
        """
        # Perform web searches
        search_results = []

        query1 = f"{brand} Deutschland Umsatz Unternehmensgröße"
        query2 = f"{brand} {industry} Deutschland Marktposition"

        result1 = await self._execute_web_search(query1)
        result2 = await self._execute_web_search(query2)

        search_results = [
            {"query": query1, "results": result1},
            {"query": query2, "results": result2},
        ]

        # Use AI to extract structured data from search results
        system_prompt = """You are a German market research analyst.
Extract company information from web search results.

Return ONLY valid JSON:
{
  "size_tier": "small" | "mid" | "large" | "enterprise",
  "revenue_range": "X-Y Mio EUR" or null,
  "market_position": "market_leader" | "challenger" | "niche",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}

Size tier guidelines:
- small: <10M EUR revenue, <50 employees
- mid: 10-100M EUR revenue, 50-500 employees
- large: 100M-1B EUR revenue, 500-5000 employees
- enterprise: >1B EUR revenue, >5000 employees"""

        user_prompt = f"""Brand: "{brand}"
Industry: "{industry}"

Web search results:
{json.dumps(search_results, ensure_ascii=False, indent=2)}

Extract company size, revenue range, and market position."""

        try:
            response = await self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )

            result = json.loads(response.choices[0].message.content)

            return WebEnrichmentResult(
                size_tier=result.get("size_tier", "mid"),
                revenue_range=result.get("revenue_range"),
                market_position=result.get("market_position", "challenger"),
                raw_data={"search_results": search_results, "ai_result": result},
            )

        except Exception as e:
            return WebEnrichmentResult(
                size_tier="mid",
                revenue_range=None,
                market_position="challenger",
                raw_data={"error": str(e)},
            )

    async def _execute_web_search(self, query: str) -> List[Dict[str, Any]]:
        """Execute a web search using configured provider.

        Supports: tavily, serpapi, duckduckgo
        """
        if self._web_search_provider == "tavily":
            return await self._search_tavily(query)
        elif self._web_search_provider == "serpapi":
            return await self._search_serpapi(query)
        else:
            return await self._search_duckduckgo(query)

    async def _search_tavily(self, query: str) -> List[Dict[str, Any]]:
        """Search using Tavily API (AI-optimized)."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": self._web_search_api_key,
                        "query": query,
                        "search_depth": "basic",
                        "max_results": 5,
                    },
                    timeout=10.0,
                )
                data = response.json()
                return data.get("results", [])
        except Exception as e:
            return [{"error": str(e)}]

    async def _search_serpapi(self, query: str) -> List[Dict[str, Any]]:
        """Search using SerpAPI (Google results)."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://serpapi.com/search",
                    params={
                        "api_key": self._web_search_api_key,
                        "q": query,
                        "hl": "de",
                        "gl": "de",
                        "num": 5,
                    },
                    timeout=10.0,
                )
                data = response.json()
                return data.get("organic_results", [])
        except Exception as e:
            return [{"error": str(e)}]

    async def _search_duckduckgo(self, query: str) -> List[Dict[str, Any]]:
        """Search using DuckDuckGo (free, no API key)."""
        try:
            # Use DuckDuckGo Instant Answer API
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.duckduckgo.com/",
                    params={
                        "q": query,
                        "format": "json",
                        "no_redirect": 1,
                    },
                    timeout=10.0,
                )
                data = response.json()

                results = []
                if data.get("Abstract"):
                    results.append({
                        "title": data.get("Heading", ""),
                        "content": data.get("Abstract", ""),
                        "url": data.get("AbstractURL", ""),
                    })
                for topic in data.get("RelatedTopics", [])[:5]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append({
                            "title": topic.get("Text", "")[:100],
                            "content": topic.get("Text", ""),
                            "url": topic.get("FirstURL", ""),
                        })
                return results
        except Exception as e:
            return [{"error": str(e)}]

    # =========================================================================
    # AI CALL #4: Proxy Candidate Scoring (Fallback)
    # =========================================================================

    async def score_proxy_candidates(
        self,
        target_brand: str,
        web_profile: WebEnrichmentResult,
        candidates: List[Dict[str, Any]],
    ) -> ProxyScoringResult:
        """AI Call #4: Score candidates against web-enriched profile.

        Scoring weights (from design doc):
        - Size tier match: 35%
        - Revenue range overlap: 30%
        - Market position match: 20%
        - KPI score range proximity: 15%

        Minimum threshold: 0.85
        Returns top 3 candidates.
        """
        system_prompt = """You are a German market research analyst.
Score brand candidates based on similarity to a target company profile.

Scoring weights:
- Size tier match: 35%
- Revenue range overlap: 30%
- Market position match: 20%
- KPI score proximity: 15%

Return ONLY valid JSON:
{
  "candidates": [
    {
      "brand_label": "brand name",
      "score": 0.0-1.0,
      "reasoning": "brief explanation",
      "size_tier_match": 0.0-1.0,
      "revenue_match": 0.0-1.0,
      "position_match": 0.0-1.0,
      "kpi_proximity": 0.0-1.0
    }
  ]
}

Only include candidates with score >= 0.85. Return top 3 maximum."""

        user_prompt = f"""Target brand: "{target_brand}"

Target profile:
- Size tier: {web_profile.size_tier}
- Revenue range: {web_profile.revenue_range or "Unknown"}
- Market position: {web_profile.market_position}

Candidate brands with their KPI scores:
{json.dumps(candidates[:50], ensure_ascii=False, indent=2)}

Score each candidate on similarity to the target profile."""

        try:
            response = await self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )

            result = json.loads(response.choices[0].message.content)

            candidates_list = []
            for c in result.get("candidates", []):
                candidates_list.append(ProxyCandidate(
                    brand_label=c.get("brand_label", ""),
                    score=c.get("score", 0.0),
                    reasoning=c.get("reasoning", ""),
                    size_tier_match=c.get("size_tier_match", 0.0),
                    revenue_match=c.get("revenue_match", 0.0),
                    position_match=c.get("position_match", 0.0),
                    kpi_proximity=c.get("kpi_proximity", 0.0),
                ))

            # Sort by score descending, take top 3
            candidates_list.sort(key=lambda x: x.score, reverse=True)

            return ProxyScoringResult(
                candidates=candidates_list[:3],
                threshold=0.85,
            )

        except Exception as e:
            return ProxyScoringResult(
                candidates=[],
                threshold=0.85,
            )

    # =========================================================================
    # AI CALL #5: Competitor Suggestion (NEW)
    # =========================================================================

    async def suggest_competitors(
        self,
        customer_brand: str,
        customer_industry: str,
        available_brands: List[str],
        brand_kpi: str,
        max_competitors: int = 10,
        customer_nielsen_brand: Optional[str] = None,
        debug_logger: Optional[Any] = None,
    ) -> CompetitorSuggestionResult:
        """AI Call #5: Suggest direct competitors from available brands.

        Model: GPT-4o-mini
        Est. time: ~300ms

        Instead of using KPI proximity algorithm, we ask the LLM to
        semantically identify which brands are direct competitors.
        """
        system_prompt = """You are a media planning expert for the German market.

Your task is to identify DIRECT COMPETITORS to a customer brand from a list of available brands.

Direct competitors MUST be:
1. In the SAME PRODUCT CATEGORY (e.g., yogurt vs yogurt, not yogurt vs cream cheese)
2. Competing for the same customer segment and purchase occasion
3. Offering similar or directly substitute products
4. From DIFFERENT corporate parents (competitors, not sister brands)
5. Relevant benchmarks for media spend comparison

CRITICAL EXCLUSION RULES:
- NEVER include brands from the same corporate parent/Konzern as the customer
  Example: If customer is "Ehrmann Almighurt", exclude ALL Ehrmann brands (Ehrmann High Protein, Grand Dessert, etc.)
- NEVER include products from different categories even if same broad sector
  Example: If customer is a yogurt brand, exclude cream cheese (Philadelphia), kids chocolate desserts (Monte), frozen pizza, etc.
- Focus on DIRECT product substitutes only

EXAMPLES of correct competitor matching:
- Yogurt brand → other yogurt brands (Müller, Danone, Zott yogurts, Landliebe yogurt)
- Frozen pizza → other frozen pizzas (Dr. Oetker, Wagner, Gustavo Gusto)
- Cream cheese → other cream cheese brands

Return ONLY valid JSON in this format:
{"competitors": ["Brand1", "Brand2", ...], "reasoning": "brief explanation of selection logic", "confidence": 0.95}"""

        # Build exclusion note if we know the corporate parent
        exclusion_note = ""
        if customer_nielsen_brand:
            # Extract corporate parent from Nielsen brand (usually first part)
            parent = customer_nielsen_brand.split("_")[0] if "_" in customer_nielsen_brand else customer_nielsen_brand
            exclusion_note = f"\n\nIMPORTANT: The customer belongs to corporate parent '{parent}'. Exclude ALL brands from {parent} (any brand containing '{parent}' in its name)."

        user_prompt = f"""Customer: "{customer_brand}"
Industry: "{customer_industry}"
KPI Focus: {brand_kpi}{exclusion_note}

Available brands in this industry (select competitors from this list only):
{json.dumps(available_brands[:200], ensure_ascii=False)}

Identify which brands are DIRECT COMPETITORS to {customer_brand}.
Focus on brands in the SAME PRODUCT CATEGORY (e.g., if customer sells yogurt, only include other yogurt brands).
Return maximum {max_competitors} competitors, most relevant first."""

        # Save prompt for debugging
        if debug_logger:
            debug_logger.save_y2_prompt(system_prompt, user_prompt)

        try:
            response = await self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )

            result = json.loads(response.choices[0].message.content)

            # Validate that returned competitors are in the available list
            valid_competitors = [
                c for c in result.get("competitors", [])
                if c in available_brands and c.lower() != customer_brand.lower()
            ]

            return CompetitorSuggestionResult(
                competitors=valid_competitors[:max_competitors],
                reasoning=result.get("reasoning", ""),
                confidence=result.get("confidence", 1.0),
            )

        except Exception as e:
            return CompetitorSuggestionResult(
                competitors=[],
                reasoning=f"Error: {str(e)}",
                confidence=0.0,
            )

    # =========================================================================
    # AI CALL #6: Produktmarke Filtering (NEW)
    # =========================================================================

    async def filter_produktmarke(
        self,
        customer_brand: str,
        nielsen_marke: str,
        produktmarke_list: List[str],
        debug_logger: Optional[Any] = None,
    ) -> ProduktmarkeFilterResult:
        """AI Call #6: Filter relevant Produktmarke sub-brands.

        Model: GPT-4o-mini
        Est. time: ~200ms

        Nielsen has multiple Produktmarke entries per Marke (e.g., for EHRMANN:
        EHRMANN_ALMIGHURT, EHRMANN_HIGH_PROTEIN, EHRMANN_GRANDDESSERT, etc.).
        We ask the LLM which sub-brands are relevant for the customer brand.
        """
        system_prompt = """You are a media planning expert for the German market.

Your task is to determine which Produktmarke (sub-brand) entries are RELEVANT for analyzing media spend for a specific customer brand.

Relevance criteria:
- YES: Directly the modelled product/brand
- YES, LIKELY: Umbrella brand campaigns that may include the modelled product (e.g., "_ALLGEMEIN", "_IMAGE")
- YES, LIKELY: Image campaigns that affect the modelled product indirectly
- MAYBE → Include: Related campaigns if close enough to the product category
- NO: Different product line (e.g., high protein vs yogurt, different food category)
- NO: Unrelated sub-brand or different target audience

When in doubt, INCLUDE the Produktmarke - it's better to have slightly more data than to miss relevant spend.

Return ONLY valid JSON in this format:
{
  "relevant": ["produktmarke1", "produktmarke2"],
  "excluded": ["produktmarke3", "produktmarke4"],
  "reasoning": {
    "PRODUKTMARKE1": "Directly the modelled brand/product",
    "PRODUKTMARKE2": "Umbrella brand that may include the product",
    "PRODUKTMARKE3": "Different product line - exclude"
  },
  "confidence": 0.95
}"""

        user_prompt = f"""We are modelling: "{customer_brand}"
Nielsen parent brand (Marke): "{nielsen_marke}"

Nielsen has these Produktmarke (sub-brand) entries under {nielsen_marke}:
{json.dumps(produktmarke_list[:50], ensure_ascii=False)}

Determine which Produktmarke entries are RELEVANT for analyzing media spend for "{customer_brand}"."""

        # Save prompt for debugging
        if debug_logger:
            debug_logger.save_n1_prompt(system_prompt, user_prompt)

        try:
            response = await self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,  # More deterministic for filtering decisions
                response_format={"type": "json_object"},
            )

            result = json.loads(response.choices[0].message.content)

            # Validate that returned produktmarke are in the original list
            valid_relevant = [
                p for p in result.get("relevant", [])
                if p in produktmarke_list
            ]
            valid_excluded = [
                p for p in result.get("excluded", [])
                if p in produktmarke_list
            ]

            return ProduktmarkeFilterResult(
                relevant=valid_relevant,
                excluded=valid_excluded,
                reasoning=result.get("reasoning", {}),
                confidence=result.get("confidence", 1.0),
            )

        except Exception as e:
            # On error, include all produktmarke (safer default)
            return ProduktmarkeFilterResult(
                relevant=produktmarke_list,
                excluded=[],
                reasoning={"error": str(e)},
                confidence=0.0,
            )


# =========================================================================
# OpenAI Function Calling with Web Search Tool
# =========================================================================

class AIWithWebSearchService(AIResolutionService):
    """Extended AI service with native OpenAI function calling for web search.

    This allows GPT-4o to decide when to call web search during conversation.
    """

    async def resolve_with_web_search(
        self,
        user_brand: str,
        user_industry: str,
        context: str = "",
    ) -> Dict[str, Any]:
        """Let GPT-4o decide when to use web search for brand resolution.

        Uses OpenAI Tools API (function calling) to enable the model to
        request web searches as needed.
        """
        system_prompt = """You are a German media planning data analyst.
You have access to a web_search tool to find company information.

When resolving unknown brands, you may search for:
1. Company size and revenue
2. Market position
3. Industry classification

Use German search queries for German market data.
Example queries:
- "{brand} Deutschland Umsatz Unternehmensgröße"
- "{brand} {industry} Marktposition"

After gathering information, provide a structured analysis."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Research the brand '{user_brand}' in the '{user_industry}' industry. Find company size, revenue range, and market position."},
        ]

        # First call - model may request web search
        response = await self.client.chat.completions.create(
            model=self.MODEL_FALLBACK,  # Use GPT-4o for tool use
            messages=messages,
            tools=[self.WEB_SEARCH_TOOL],
            tool_choice="auto",
        )

        # Process tool calls if any
        assistant_message = response.choices[0].message
        tool_calls = assistant_message.tool_calls

        if tool_calls:
            messages.append(assistant_message)

            # Execute each tool call
            for tool_call in tool_calls:
                if tool_call.function.name == "web_search":
                    args = json.loads(tool_call.function.arguments)
                    query = args.get("query", "")

                    # Execute web search
                    search_results = await self._execute_web_search(query)

                    # Add tool result to messages
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(search_results, ensure_ascii=False),
                    })

            # Get final response after tool use
            final_response = await self.client.chat.completions.create(
                model=self.MODEL_FALLBACK,
                messages=messages,
                response_format={"type": "json_object"},
            )

            return json.loads(final_response.choices[0].message.content)

        # No tool calls - return direct response
        return {"response": assistant_message.content}
