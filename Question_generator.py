from itertools import chain
import json
import logging
from typing import List, Dict, Any, Tuple, Set
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from qdrant_service import qdrant_service
import re

logger = logging.getLogger(__name__)

# Constants for regeneration
MAX_REGENERATION_ATTEMPTS = 3

class QuestionGenerator:
    def __init__(self, llm_model: str = "gpt-4o"):
        self.llm = ChatOpenAI(model=llm_model, temperature=0.4)
        self.learning_service: Any = None  # Will be set to LearningDatabaseService at startup
        self.qdrant_service = qdrant_service

        # MCQ Generation Prompt
        self.mcq_prompt = ChatPromptTemplate.from_template("""
You are an expert educational question generator for a financial literacy platform called MoneyQuest. Based on the provided learning content, generate {num_questions} high-quality multiple choice questions.

LEVEL INFORMATION:
- Level_description: {level_description}
- Level: {level_title}
- Difficulty: {difficulty}
                                                           
LEARNING CONTENT:
                                                           
{context}

REQUIREMENTS:
1. Each question must have exactly 4 options (A, B, C, D)
2. Only ONE option should be correct
3. Wrong options should be plausible but incorrect based on the Learning content
4. Provide clear explanations for ALL options (why correct/incorrect)
5. Questions should test comprehension, application, and critical thinking
6. Questions should align with the difficulty level: {difficulty}
   - Easy: Basic recall and comprehension
   - Medium: Application and analysis
   - Hard: Evaluation and synthesis
7. All content must be grounded in the provided learning material
8. Ensure the correct answers of the {num_questions} questions are not monotonic, they should be randomized enough to not be predictable.
9. Above all else ensure the questions generated, irrespective of the difficulty is young adult friendly and not bulky in text.
10. **Unique Options:** All four options (A, B, C, D) must be completely unique and distinct from each other. There must be NO repetition or duplication of advice, strategies, or recommendations across the options. Each option must present a genuinely different approach or solution to the scenario.


OUTPUT FORMAT (JSON):
[
  {{
    "question": "Question text here?",
    "options": {{
      "A": "Option A text",
      "B": "Option B text", 
      "C": "Option C text",
      "D": "Option D text"
    }},
    "correct_answer": "A", NOTE: option A is an Example not a requirement
    "explanations": {{
      "A": "Correct - explanation based on the learning content",
      "B": "Incorrect - explanation why this is wrong",
      "C": "Incorrect - explanation why this is wrong",
      "D": "Incorrect - explanation why this is wrong"
    }},
    "difficulty": "{difficulty}"
  }}
]

Generate exactly {num_questions} questions in valid JSON format:
""")
        self.matching_prompt = ChatPromptTemplate.from_template("""
You are an expert educational content creator for MoneyQuest. 
Based on the provided learning content, generate a "Matching" exercise consisting of {num_pairs} pairs.

LEVEL INFORMATION:
- Level_description: {level_description}
- Level: {level_title}
- Difficulty: {difficulty}
- Matching Type: {matching_type_instruction}

LEARNING CONTENT:
{context}

TASK:
Generate a list of pairs where items in "Column A" match items in "Column B".
STRICT CONSTRAINT
{specific_instruction}
{excluded_terms_instruction}

REQUIREMENTS:
1. Generate exactly {num_pairs} correct pairs.
2. Ensure the pairs are distinct and not ambiguous.
3. The content must be strictly based on the provided Learning Content.
4. Difficulty: {difficulty}
   - Easy: Direct definitions.
   - Medium: Concepts and examples.
   - Hard: Complex relationships or scenarios.
5. Ensure the correct answers of the {num_pairs} questions are not monothonic, they should be randomized enough to not be predictable.
6. Above all else ensure the questions generated, irrespective of the difficulty is young adult friendly and not bulky in text.
7. **Unique Options:** All four options (A, B, C, D) must be completely unique and distinct from each other. There must be NO repetition or duplication of advice, strategies, or recommendations across the options. Each option must present a genuinely different approach or solution to the scenario.
8. **CRITICAL - Unique Terms:** Each term in Column A must be unique. Do NOT reuse any terms that have already been used.

OUTPUT FORMAT (JSON):
{{
  "title": "Match the following terms related to {level_title}",
    "types": "{matching_type_code}",
  "pairs": [
    {{
      "id": 1,
      "column_a": "Item 1",
      "column_b": "Matching Item 1"
    }},
    {{
      "id": 2,
      "column_a": "Item 2",
      "column_b": "Matching Item 2"
    }}
  ]
}}
""")                                                                
        self.fitg_prompt = ChatPromptTemplate.from_template("""
You are an expert educational question generator for a financial literacy platform called MoneyQuest. Based on the provided learning content, generate {num_questions} high-quality fill in the gap questions.

LEVEL INFORMATION:
- Level_description: {level_description}
- Level: {level_title}
- Difficulty: {difficulty}
                                                           
LEARNING CONTENT:
                                                           
{context}

REQUIREMENTS:
1. Each question must have exactly 4 options (A, B, C, D)
2. Each question must be a sentence or short paragraph with a **single blank space** (`___`) where the answer belongs, the question should not be ambigious or have possiblity of double answers.
3. Only ONE option should be correct,the answer must be a **single word or a short phrase** (maximum 4 words) derived  from the learning content.
4. Wrong options should be plausible but incorrect based on the Learning content
5. Provide clear explanations for ALL options (why correct/incorrect)
6. Questions should test comprehension, application, and critical thinking
7. Questions should align with the difficulty level: {difficulty}
   - Easy: Basic recall and comprehension
   - Medium: Application and analysis
   - Hard: Evaluation and synthesis
8. All content must be grounded in the provided learning material
9. Ensure the correct answers of the {num_questions} questions are not monotonic, they should be randomized enough to not be predictable.
10. Above all else ensure the questions generated, irrespective of the difficulty is young adult friendly and not bulky in text.
11. **Unique Options:** All four options (A, B, C, D) must be completely unique and distinct from each other. There must be NO repetition or duplication of advice, strategies, or recommendations across the options. Each option must present a genuinely different approach or solution to the scenario.


OUTPUT FORMAT (JSON):
[
  {{
    "question": "Question text here?",
    "options": {{
      "A": "Option A text",
      "B": "Option B text", 
      "C": "Option C text",
      "D": "Option D text"
    }},
    "correct_answer": "A", NOTE: option A is an Example not a requirement
    "explanations": {{
      "A": "Correct - explanation based on the learning content",
      "B": "Incorrect - explanation why this is wrong",
      "C": "Incorrect - explanation why this is wrong",
      "D": "Incorrect - explanation why this is wrong"
    }},
    "difficulty": "{difficulty}"
  }}
]

Generate exactly {num_questions} questions in valid JSON format:
""")
        self.sbq_prompt = ChatPromptTemplate.from_template("""
        You are an expert educational question generator for a financial literacy platform called MoneyQuest. You must generate {num_questions} high-quality, real-world, scenario-based questions **set within the Nigerian economic and cultural context**.

LEVEL INFORMATION:
- Level_description: {level_description}
- Level: {level_title}
- Difficulty: {difficulty}
                                                                
**CRITICAL CONSTRAINTS - STRICTLY FOLLOW:**
1. You may ONLY use concepts, terms, vocabulary, examples, and scenarios that are EXPLICITLY mentioned in the LEARNING CONTENT below.
2. DO NOT introduce ANY external concepts, tools, apps, websites, products, brands, or ideas not present in the learning content.
3. DO NOT reference real-world financial tools (Opay, PayPal, banking apps, etc.) unless they appear in the learning content.
4. DO NOT add educational value beyond what is explicitly taught in the content.
5. Every term in your question MUST be traceable to the learning content.
6. If the learning content only mentions "coins" - do not introduce "bills", "credit cards", or "digital money".
7. Scenarios must use ONLY the exact or similar examples (if provided), characters, or situations from the learning content.

**VALIDATION CHECKLIST (apply to each question):**
- [ ] Every noun/concept appears in the learning content
- [ ] Every action/verb reflects what was taught
- [ ] The scenario mirrors examples from the content
- [ ] No assumptions beyond what is explicitly stated

LEARNING CONTENT (this is your ONLY source of truth):
\"\"\"
{context}
\"\"\"
                                                           
REQUIREMENTS:
1. **Scenario Creation:** Each item must begin with a narrative describing a financial situation or dilemma faced by an individual or small business in **Nigeria**. The scenario must use appropriate **Nigerian terms, locations, or financial instruments** (e.g., *POS agent*, *Naira*, *Lagos market*, *BVN*, *contributing to Ajo/Esusu*, *NIBSS transfer*, *quick loan apps*, *side hustle*). 

   **Diverse Professions to Feature:**
   - **9-5 Employees:** Civil servants, bankers, teachers, nurses, HR managers, accountants, administrative staff
   - **Entrepreneurs/Founders:** Tech startup founders, restaurant owners, fashion designers, beauty salon owners, logistics business owners
   - **Freelancers/Gig Workers:** Graphic designers, content writers, photographers, video editors, social media managers, virtual assistants
   - **Sales & Marketing:** Real estate agents, insurance agents, sales representatives, brand ambassadors, affiliate marketers
   - **Tech Professionals:** Software developers, product managers, UI/UX designers, data analysts, cybersecurity specialists
   - **Consultants:** Business consultants, financial advisors, HR consultants, marketing strategists
   - **Creative Professionals:** Musicians, artists, fashion stylists, makeup artists, event planners
   - **Trade & Services:** Mechanics, electricians, tailors, caterers, hairdressers, delivery riders (Gokada/ORide)
   - **Students & Youth:** University students with side hustles, NYSC members, recent graduates, interns
   - **Small Business Owners:** Market traders, provision store owners, food vendors, boutique owners, phone repair technicians
   
   The scenario must be grounded in the given context and reflect realistic financial challenges these professionals face in Nigeria.

2. **Question Focus:** The question must ask the user to **analyze, advise, or decide** the best course of action based on the scenario and the LEARNING CONTENT provided.

3. **Multiple Choice Options:** Each question must have exactly 4 options (A, B, C, D).

4. **Options Plausibility:** Wrong options should be **plausible but incorrect** or suboptimal advice for the Nigerian context.

5. **Clear Explanations:** Provide clear explanations for ALL options (why correct/incorrect).

6. **Difficulty Alignment:** Questions should align with the difficulty level: {difficulty}
   - Easy: Basic recall applied to a simple, common Nigerian scenario.
   - Medium: Analysis of common financial decisions (e.g., investment vs. debt repayment, budgeting with fluctuating income).
   - Hard: Evaluation of complex strategies (e.g., navigating inflation, FX instability, complex business decisions, tax optimization).

7. **Content Grounding:** All content and solutions must be strictly grounded in the provided learning material.

8. **Randomization:** Ensure the correct answers of the {num_questions} questions are not monotonic; they should be randomized enough to not be predictable.

9. **Scenario Relevance:** On no account should a scenario question be generated outside the scope and context given by the learning content. Both the scenario and question must be inspired from the learning content.

10. **Young Adult-Friendly & Concise:** Above all else, ensure the questions generated, irrespective of difficulty, are young adult-friendly and not bulky in text. Keep scenarios clear, relatable, and age-appropriate.

11. **Unique Options:** All four options (A, B, C, D) must be completely unique and distinct from each other. There must be NO repetition or duplication of advice, strategies, or recommendations across the options. Each option must present a genuinely different approach or solution to the scenario.

OUTPUT FORMAT (JSON):
[
  {{{{
    "scenario": "Chidi is a software developer in Lagos earning ₦450,000 monthly from his 9-5 job. He also does freelance projects that bring in ₦150,000-₦300,000 irregularly. He wants to save for a laptop upgrade (₦800,000) but also needs to manage his inconsistent income flow.",
    "question": "What is the most practical savings strategy for Chidi given his variable income?",
    "options": {{{{ 
      "A": "Save a fixed ₦100,000 monthly from his salary only, and spend all freelance income as it comes.",
      "B": "Save 20% of his total income (both salary and freelance) each month into a dedicated savings account.",
      "C": "Wait until he has a high-earning freelance month, then save everything at once.",
      "D": "Take a quick loan to buy the laptop immediately and pay back gradually."
    }}}},
    "correct_answer": "B",
    "explanations": {{{{
      "A": "Incorrect - This ignores the freelance income entirely, missing an opportunity to accelerate savings from variable sources.",
      "B": "Correct - This approach accounts for both regular and irregular income streams, creating a flexible but disciplined savings habit that adapts to Chidi's total earnings each month.",
      "C": "Incorrect - Procrastinating on savings is unreliable and may lead to spending the money impulsively when it arrives.",
      "D": "Incorrect - Taking unnecessary debt for a purchase he can save for creates interest obligations and financial stress."
    }}}},
    "difficulty": "{difficulty}"
  }}}}
]
**FINAL CHECK:** Before outputting, verify each question could be answered by someone who ONLY read the learning content above, with NO prior knowledge.
Generate exactly {num_questions} questions in valid JSON format:
        """)

    def format_context(self, docs,fallback_description="") -> str:
        """Format retrieved documents into context string."""
        if not docs:
            return "No context available."
        
        context_parts = []
        for i, doc in enumerate(docs[:5], 1):  # Limit to 5 docs for context
            source = doc.metadata.get('source', 'Unknown')
            level_title = doc.metadata.get('level_title', 'Unknown Level')
            meta_desc = doc.metadata.get('level_description')
            final_desc = meta_desc if meta_desc else fallback_description
            
            content = doc.page_content  
            context_parts.append(
                f"Content {i} - Level Description: {final_desc}, Level: {level_title}\n"
                f"Source: {source}\n{content}"
            )
        
        return "\n\n---\n\n".join(context_parts)

    def _parse_json_response(self, response: str) -> Any:
        """Parse JSON response from LLM with fallback strategies."""
        try:
            questions = json.loads(response)
            return questions
        except json.JSONDecodeError as e:
            logger.warning(f"Initial JSON parsing failed: {e}")
            # Try to extract JSON from response if it's wrapped in text
            
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                try:
                    questions = json.loads(json_match.group())
                    return questions
                except json.JSONDecodeError:
                   pass
            cleaned = response.strip()   
            if cleaned.startswith('```json'):
                cleaned = cleaned[7:]
            if cleaned.endswith('```'):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            try:
                questions = json.loads(cleaned)
                return questions
            except json.JSONDecodeError as e2:
                logger.error(f"Final JSON parsing attempt failed: {e2}")
                raise ValueError("Could not parse questions from LLM response")


    def _parse_matching_response(self, response: str) -> Dict[str,Any]:
        data = None

        logger.debug("Parsing strategy 1: Direct JSON Parse.")
        try:
            data = json.loads(response)
            logger.info("Parsing successful with Strategy 1: Direct JSON Parse.")
        except json.JSONDecodeError as e:
            logger.debug(f"Strategy 1 failed: Not direct JSON. Error: {e}")
            pass
            
        # Strategy 2: Clean Markdown
        if data is None:
            logger.debug("Parsing strategy 2: Clean Markdown code block.")
            cleaned = response.strip()
            # Remove code block markers (start)
            if "```" in cleaned:
                cleaned = re.sub(r'^```[a-zA-Z]*\s*', '', cleaned)
                cleaned = re.sub(r'\s*```$', '', cleaned)
                try:
                    data = json.loads(cleaned)
                    logger.info("Parsing successful with Strategy 2: Cleaned Markdown.")
                except json.JSONDecodeError as e:
                    logger.debug(f"Strategy 2 failed: Cleaned text is not JSON. Error: {e}")
                    pass
           
        # Strategy 3: Regex for JSON Object { ... }
        if data is None:
            logger.debug("Parsing strategy 3: Regex search for JSON Object {...}.")
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                    logger.info("Parsing successful with Strategy 3: Regex for Object.")

                except json.JSONDecodeError as e:
                    logger.debug(f"Strategy 3 failed: Regex match not valid JSON. Error: {e}")
                    pass


        # Strategy 4: Regex for JSON Array [ ... ] (in case LLM wrapped it)
        if data is None:
            json_match = re.search(r'\[[\s\S]*\]', response)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                    logger.info("Parsing successful with Strategy 4: Regex for Array.")
                except json.JSONDecodeError:
                    pass
   
        if data is None:
            logger.error(f"FATAL PARSING ERROR: All strategies failed to parse the response. Raw response start: {response[:100]}...")
            raise ValueError("Could not parse matching questions from LLM response")

        # Post-processing: Handle List vs Dict
        if isinstance(data, list):
            if len(data) > 0 and isinstance(data[0], dict):
                # If LLM returned [ { ... } ], take the first item
                logger.warning("Post-processing: LLM returned a list, extracting the first dictionary item.")
                return data[0]
            elif len(data) == 0:
                logger.error("Received empty list from LLM response.")
                raise ValueError("Received empty list from LLM")
            else:
                logger.error(f"Received list of {type(data[0])} but expected dictionary structure. Data: {data}")
                raise ValueError(f"Received list of {type(data[0])} but expected dictionary structure")
                 
        if not isinstance(data, dict):
            logger.error(f"Expected dictionary response, but final parsed data is {type(data)}. Data: {data}")
            raise ValueError(f"Expected dictionary response, got {type(data)}")

        logger.info("Successfully parsed LLM response as a dictionary.")    
        return data

    async def generate_questions_for_level(self, level_id: str, level_description: str="Unknown Level Description", level_title: str="Unknown Level", num_questions: int = 5, difficulty: str = "medium") -> List[Dict[str, Any]]:
        """
        Generate MCQs for a specific level.
        
        Args:
            level_id: Unique identifier for the level
            level_description: Description of the level
            level_title: Title of the level
            num_questions: Number of questions to generate
            difficulty: Question difficulty ("easy", "medium", "hard")
        
        Returns:
            List of MCQ dictionaries with level context
        """

        try:
            retriever = self.qdrant_service.get_retriever()
            if not retriever:
                raise RuntimeError("Qdrant retriever not initialized")
            
            search_kwargs = {
                "k": min(num_questions * 3, 15)
                # "filter": {"level_id": level_id}
            }
            docs = []
            try:

                retriever_with_filter = self.qdrant_service.get_retriever(search_kwargs=search_kwargs)
                docs = retriever_with_filter.invoke(level_description)
            except Exception as e:
                logger.warning(f"Filter search failed with error: {e}")
                docs =[]
            logger.info(f"Retrieved {len(docs)} documents for level_title: {level_title}")

            if not docs:
                logger.warning(f"Metadata filter failed, using semantic search  for level_title: {level_title}")
                search_kwargs = {"k": min(num_questions * 3, 15)}
                retriever_with_kwargs = qdrant_service.get_retriever(search_kwargs=search_kwargs)
                docs = retriever_with_kwargs.invoke(f"{level_description} {level_title}")       

            if not docs:
                logger.warning(f"⚠️ No RAG docs found. Generating based on description: '{level_description}'")
                context = (
                    f"Topic: {level_title}\n"
                    f"Description: {level_description}\n"
                    "Instruction: The database has no specific text for this topic. "
                    "Generate general educational questions based strictly on the Topic and Description provided above."
                )
            else:
                context = self.format_context(docs, fallback_description=level_description)
            
            context = self.format_context(docs,fallback_description=level_description)
            chain = self.mcq_prompt | self.llm | StrOutputParser()
            response = await chain.ainvoke({
                "context": context,
                "num_questions": num_questions,
                "level_description":level_description,
                "level_title":level_title,
                "difficulty":difficulty
            })
            questions =  self._parse_json_response(response)
            validated_data = self.validate_questions(questions)
            for q in validated_data:
                q["level_id"]=level_id
                if "difficulty" not in q:
                    q["difficulty"]=difficulty
                logger.info(f"Successfully generated {len(validated_data)} questions for level_id: {level_id}")
            return validated_data
        except Exception as e:
            logger.error(f"Error generating questions for level_id: {level_id}: {e}")
            raise
    
    async def generate_scenario_questions_for_level(self, level_id: str, level_description: str="Unknown Level Description", level_title: str="Unknown Level", num_questions: int = 5, difficulty: str = "medium") -> List[Dict[str, Any]]:
        """
        Generate MCQs for a specific level.
        
        Args:
            level_id: Unique identifier for the level
            level_description: Description of the level
            level_title: Title of the level
            num_questions: Number of questions to generate
            difficulty: Question difficulty ("easy", "medium", "hard")
        
        Returns:
            List of MCQ dictionaries with level context
        """

        try:
            retriever = self.qdrant_service.get_retriever()
            if not retriever:
                raise RuntimeError("Qdrant retriever not initialized")
            
            search_kwargs = {
                "k": min(num_questions * 3, 15)
                # "filter": {"level_id": level_id}
            }
            docs = []
            try:

                retriever_with_filter = self.qdrant_service.get_retriever(search_kwargs=search_kwargs)
                docs = retriever_with_filter.invoke(level_description)
            except Exception as e:
                logger.warning(f"Filter search failed with error: {e}")
                docs =[]
            logger.info(f"Retrieved {len(docs)} documents for level_title: {level_title}")

            if not docs:
                logger.warning(f"Metadata filter failed, using semantic search  for level_title: {level_title}")
                search_kwargs = {"k": min(num_questions * 3, 15)}
                retriever_with_kwargs = qdrant_service.get_retriever(search_kwargs=search_kwargs)
                docs = retriever_with_kwargs.invoke(f"{level_description} {level_title}")       

            if not docs:
                logger.warning(f"⚠️ No RAG docs found. Generating based on description: '{level_description}'")
                context = (
                    f"Topic: {level_title}\n"
                    f"Description: {level_description}\n"
                    "Instruction: The database has no specific text for this topic. "
                    "Generate general educational questions based strictly on the Topic and Description provided above."
                )
            else:
                context = self.format_context(docs, fallback_description=level_description)
            
            context = self.format_context(docs,fallback_description=level_description)
            chain = self.sbq_prompt | self.llm | StrOutputParser()
            response = await chain.ainvoke({
                "context": context,
                "num_questions": num_questions,
                "level_description":level_description,
                "level_title":level_title,
                "difficulty":difficulty
            })
            questions =  self._parse_json_response(response)
            validated_data = self.validate_scenario_questions(questions)
            for q in validated_data:
                q["level_id"]=level_id
                if "difficulty" not in q:
                    q["difficulty"]=difficulty
                logger.info(f"Successfully generated {len(validated_data)} questions for level_id: {level_id}")
            return validated_data
        except Exception as e:
            logger.error(f"Error generating questions for level_id: {level_id}: {e}")
            raise

    def _filter_duplicate_pairs(self, pairs: List[Dict[str, Any]], existing_terms: Set[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Filter matching pairs to identify unique and duplicate pairs.
        Checks both against existing terms in DB and within the batch itself.
        
        Args:
            pairs: List of pair dictionaries with 'column_a' and 'column_b'
            existing_terms: Set of existing column_a values (normalized to lowercase)
            
        Returns:
            Tuple of (unique_pairs, duplicate_pairs)
        """
        unique_pairs = []
        duplicate_pairs = []
        seen_terms = set()  # Track terms within this batch
        
        for pair in pairs:
            col_a = pair.get("column_a", "").strip().lower()
            
            if col_a in existing_terms or col_a in seen_terms:
                duplicate_pairs.append(pair)
                logger.debug(f"Detected duplicate term: {col_a}")
            else:
                unique_pairs.append(pair)
                seen_terms.add(col_a)
        
        return unique_pairs, duplicate_pairs

    async def generate_matching_questions_for_level(self, level_id: str, level_description: str="Unknown Level Description", level_title: str="Unknown Level", num_pairs: int = 5, difficulty: str = "medium", matching_type: str = "term_definition") -> Dict[str, Any]:
        """
        Generate Matching questions for a specific level with uniqueness verification.
        If duplicates are detected, regenerates until all pairs are unique or max attempts reached.
        
        Args:
            level_id: Unique identifier for the level
            level_description: Description of the level
            level_title: Title of the level
            num_pairs: Number of pairs to generate
            difficulty: Question difficulty ("easy", "medium", "hard")
            matching_type: the matching question type (concept_example, term->definition)
        
        Returns:
            Dictionary containing matching questions with level context
        """

        try:
            if matching_type == "concept_example":
                matching_type_instruction = "Concept -> Example"
                specific_instruction = "Column A must be a general Concept or Category rooted in the given context. Column B must be a specific real-world Example of that concept, note: the example should be a single word or phrase."
                title_context = "concepts to their examples"
                matching_type_code = "concept_example"
            else:
                # Default to term_definition
                matching_type_instruction = "Term -> Definition"
                specific_instruction = "Column A must be a specific Term or Keyword rooted in the given context. Column B must be its clear, very concise Definition."
                title_context = "terms to their definitions"
                matching_type_code = "term_definition"

            # Get existing terms from database for uniqueness check
            existing_terms = set()
            if self.learning_service:
                existing_terms = self.learning_service.get_existing_terms_for_level(level_id)
                logger.info(f"Found {len(existing_terms)} existing terms for level {level_id}")
            
            retriever = self.qdrant_service.get_retriever()
            if not retriever:
                raise RuntimeError("Qdrant retriever not initialized")
            
            search_kwargs = {
                "k": min(num_pairs * 3, 15)
            }
            docs = []
            try:
                retriever_with_filter = self.qdrant_service.get_retriever(search_kwargs=search_kwargs)
                docs = retriever_with_filter.invoke(level_description)
            except Exception as e:
                logger.warning(f"Filter search failed with error: {e}")
                docs = []
            logger.info(f"Retrieved {len(docs)} documents for level_title: {level_title}")

            if not docs:
                logger.warning(f"Metadata filter failed, using semantic search for level_title: {level_title}")
                search_kwargs = {"k": min(num_pairs * 3, 15)}
                retriever_with_kwargs = qdrant_service.get_retriever(search_kwargs=search_kwargs)
                docs = retriever_with_kwargs.invoke(f"{level_description} {level_title}")       

            if not docs:
                logger.warning(f"⚠️ No RAG docs found. Generating based on description: '{level_description[:50]}'")
                context = (
                    f"Topic: {level_title}\n"
                    f"Description: {level_description}\n"
                    "Instruction: The database has no specific text for this topic. "
                    "Generate general educational questions based strictly on the Topic and Description provided above."
                )
            else:
                context = self.format_context(docs, fallback_description=level_description)
            
            context = self.format_context(docs, fallback_description=level_description)
            chain = self.matching_prompt | self.llm | StrOutputParser()
            
            # Track all unique pairs collected across regeneration attempts
            all_unique_pairs = []
            all_excluded_terms = set(existing_terms)  # Start with existing DB terms
            pairs_needed = num_pairs
            attempt = 0
            
            while pairs_needed > 0 and attempt < MAX_REGENERATION_ATTEMPTS:
                attempt += 1
                logger.info(f"Generation attempt {attempt}/{MAX_REGENERATION_ATTEMPTS}: requesting {pairs_needed} pairs")
                
                # Build excluded terms instruction
                if all_excluded_terms:
                    excluded_list = ", ".join(sorted(all_excluded_terms)[:50])  # Limit to 50 to avoid prompt bloat
                    excluded_terms_instruction = f"\n**IMPORTANT - EXCLUDED TERMS:** Do NOT use these terms in Column A (they are already used): [{excluded_list}]"
                else:
                    excluded_terms_instruction = ""
                
                response = await chain.ainvoke({
                    "context": context,
                    "num_pairs": pairs_needed,
                    "level_description": level_description,
                    "level_title": level_title,
                    "difficulty": difficulty,
                    "matching_type_instruction": matching_type_instruction,
                    "specific_instruction": specific_instruction,
                    "title_context": title_context,
                    "matching_type_code": matching_type_code,
                    "excluded_terms_instruction": excluded_terms_instruction
                })
                
                matching_data = self._parse_matching_response(response)
                validated_data = self._validate_matching_questions(matching_data)
                generated_pairs = validated_data.get("pairs", [])
                
                # Filter for unique pairs
                unique_pairs, duplicate_pairs = self._filter_duplicate_pairs(generated_pairs, all_excluded_terms)
                
                if duplicate_pairs:
                    logger.warning(f"Attempt {attempt}: Found {len(duplicate_pairs)} duplicate pairs, kept {len(unique_pairs)} unique pairs")
                
                # Add unique pairs to our collection
                all_unique_pairs.extend(unique_pairs)
                
                # Update excluded terms with newly used terms
                for pair in unique_pairs:
                    all_excluded_terms.add(pair.get("column_a", "").strip().lower())
                
                # Calculate how many more pairs we need
                pairs_needed = num_pairs - len(all_unique_pairs)
                
                if pairs_needed <= 0:
                    logger.info(f"Successfully collected {len(all_unique_pairs)} unique pairs after {attempt} attempt(s)")
                    break
                    
            # If we still need pairs after max attempts, log a warning
            if pairs_needed > 0:
                logger.warning(f"Could only generate {len(all_unique_pairs)} unique pairs out of {num_pairs} requested after {MAX_REGENERATION_ATTEMPTS} attempts")
            
            # Re-index pair IDs
            for idx, pair in enumerate(all_unique_pairs):
                pair["id"] = idx + 1
            
            # Build final result
            final_data = {
                "title": validated_data.get("title", f"Match the following {title_context}"),
                "types": matching_type_code,
                "pairs": all_unique_pairs,
                "level_id": level_id,
                "difficulty": difficulty,
                "type": matching_type_code,
                "generation_stats": {
                    "requested": num_pairs,
                    "generated": len(all_unique_pairs),
                    "attempts": attempt
                }
            }
            
            logger.info(f"Successfully generated {len(all_unique_pairs)} unique matching pairs for level_id: {level_id}")
            return final_data
            
        except Exception as e:
            logger.error(f"Error generating questions for level_id: {level_id}: {e}")
            raise        

    async def generate_fitg_questions_for_level(self, level_id: str, level_description: str="Unknown Level Description", level_title: str="Unknown Level", num_questions: int = 5, difficulty: str = "medium") -> List[Dict[str, Any]]:
        """
        Generate MCQs for a specific level.
        
        Args:
            level_id: Unique identifier for the level
            level_description: Description of the level
            level_title: Title of the level
            num_questions: Number of questions to generate
            difficulty: Question difficulty ("easy", "medium", "hard")
        
        Returns:
            List of MCQ dictionaries with level context
        """

        try:
            retriever = self.qdrant_service.get_retriever()
            if not retriever:
                raise RuntimeError("Qdrant retriever not initialized")
            
            search_kwargs = {
                "k": min(num_questions * 3, 15)
                # "filter": {"level_id": level_id}
            }
            docs = []
            try:

                retriever_with_filter = self.qdrant_service.get_retriever(search_kwargs=search_kwargs)
                docs = retriever_with_filter.invoke(level_description)
            except Exception as e:
                logger.warning(f"Filter search failed with error: {e}")
                docs =[]
            logger.info(f"Retrieved {len(docs)} documents for level_title: {level_title}")

            if not docs:
                logger.warning(f"Metadata filter failed, using semantic search  for level_title: {level_title}")
                search_kwargs = {"k": min(num_questions * 3, 15)}
                retriever_with_kwargs = qdrant_service.get_retriever(search_kwargs=search_kwargs)
                docs = retriever_with_kwargs.invoke(f"{level_description} {level_title}")       

            if not docs:
                logger.warning(f"⚠️ No RAG docs found. Generating based on description: '{level_description}'")
                context = (
                    f"Topic: {level_title}\n"
                    f"Description: {level_description}\n"
                    "Instruction: The database has no specific text for this topic. "
                    "Generate general educational questions based strictly on the Topic and Description provided above."
                )
            else:
                context = self.format_context(docs, fallback_description=level_description)
            
            context = self.format_context(docs,fallback_description=level_description)
            chain = self.fitg_prompt | self.llm | StrOutputParser()
            response = await chain.ainvoke({
                "context": context,
                "num_questions": num_questions,
                "level_description":level_description,
                "level_title":level_title,
                "difficulty":difficulty
            })
            questions =  self._parse_json_response(response)
            validated_data = self.validate_questions(questions)
            for q in validated_data:
                q["level_id"]=level_id
                if "difficulty" not in q:
                    q["difficulty"]=difficulty
                logger.info(f"Successfully generated {len(validated_data)} questions for level_id: {level_id}")
            return validated_data
        except Exception as e:
            logger.error(f"Error generating questions for level_id: {level_id}: {e}")
            raise
    
    async def generate_questions(self, topic: str, num_questions: int = 5, level_description: str="Unknown Level Description") -> List[Dict[str, Any]]:
        """
        Generate MCQs based on a topic/query.
        
        Args:
            topic: The topic or query to generate questions about
            num_questions: Number of questions to generate
        
        Returns:
            List of MCQ dictionaries
        """
        try:
            logger.warning("Using legacy generate_questions method. Consider using generate_questions_for_level()")
          
            # Retrieve relevant documents from vector store
            retriever = qdrant_service.get_retriever()
            if not retriever:
                raise RuntimeError("Qdrant retriever not initialized")
            
            # Get more documents for better question variety
            search_kwargs = {"k": min(num_questions * 3, 15)}
            retriever_with_kwargs = qdrant_service.get_retriever(search_kwargs=search_kwargs)
            
            docs = retriever_with_kwargs.invoke(topic)
            logger.info(f"Retrieved {len(docs)} documents for topic: {topic}")
            
            if not docs:
                raise ValueError(f"No relevant documents found for topic: {topic}")
            
            # Format context
            context = self.format_context(docs, fallback_description=level_description)
            
            # Generate questions using LLM
            chain = self.mcq_prompt | self.llm | StrOutputParser()
            
            response = await chain.ainvoke({
                "context": context,
                "num_questions": num_questions
            })
            
            return await self.generate_questions_for_level(
                level_id=f"legacy_{topic}",
                level_description="General",
                level_title=topic,
                num_questions=num_questions
            )
           
        except Exception as e:
            logger.error(f"Error generating questions: {e}", exc_info=True)
            raise

    def validate_questions(self, questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate and clean generated questions."""
        valid_questions = []
        
        for i, q in enumerate(questions):
            try:
                # Check required fields
                required_fields = ['question', 'options', 'correct_answer', 'explanations']
                if not all(field in q for field in required_fields):
                    logger.warning(f"Question {i+1} missing required fields")
                    continue
                
                # Check options format
                if not isinstance(q['options'], dict) or len(q['options']) != 4:
                    logger.warning(f"Question {i+1} has invalid options format")
                    continue
                
                # Check correct answer is valid
                if q['correct_answer'] not in q['options']:
                    logger.warning(f"Question {i+1} has invalid correct answer")
                    continue
                
                # Check explanations
                if not isinstance(q['explanations'], dict) or len(q['explanations']) != 4:
                    logger.warning(f"Question {i+1} has invalid explanations format")
                    continue
                
                valid_questions.append(q)
                
            except Exception as e:
                logger.warning(f"Error validating question {i+1}: {e}")
                continue
        
        logger.info(f"Validated {len(valid_questions)} out of {len(questions)} questions")
        return valid_questions
    
    def validate_scenario_questions(self, questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate and clean generated questions."""
        valid_questions = []
        
        for i, q in enumerate(questions):
            try:
                # Check required fields
                required_fields = ['scenario','question', 'options', 'correct_answer', 'explanations']
                if not all(field in q for field in required_fields):
                    logger.warning(f"Question {i+1} missing required fields")
                    continue
                
                # Check options format
                if not isinstance(q['options'], dict) or len(q['options']) != 4:
                    logger.warning(f"Question {i+1} has invalid options format")
                    continue
                
                # Check correct answer is valid
                if q['correct_answer'] not in q['options']:
                    logger.warning(f"Question {i+1} has invalid correct answer")
                    continue
                
                # Check explanations
                if not isinstance(q['explanations'], dict) or len(q['explanations']) != 4:
                    logger.warning(f"Question {i+1} has invalid explanations format")
                    continue
                
                valid_questions.append(q)
                
            except Exception as e:
                logger.warning(f"Error validating question {i+1}: {e}")
                continue
        
        logger.info(f"Validated {len(valid_questions)} out of {len(questions)} questions")
        return valid_questions
    
    def _validate_matching_questions(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates and cleans the matching questions structure.
        Ensures 'pairs' exist, have content, and re-indexes IDs.
        """
        if not isinstance(data, dict):
            raise ValueError("Matching data must be a dictionary")

        # Ensure required top-level keys exist
        if "pairs" not in data:
            raise ValueError("Missing 'pairs' key in matching data")
        
        if not isinstance(data["pairs"], list):
            raise ValueError("'pairs' must be a list")

        valid_pairs = []
        
        # Validate individual pairs
        for index, pair in enumerate(data["pairs"]):
            if not isinstance(pair, dict):
                logger.warning(f"Skipping invalid pair format at index {index}: {pair}")
                continue

            # Check for column_a and column_b (case insensitive check if needed, but prompt enforces specific keys)
            col_a = pair.get("column_a")
            col_b = pair.get("column_b")

            # Ensure they are strings and not empty
            if not col_a or not isinstance(col_a, str) or not col_a.strip():
                logger.warning(f"Skipping pair with empty Column A at index {index}")
                continue
                
            if not col_b or not isinstance(col_b, str) or not col_b.strip():
                logger.warning(f"Skipping pair with empty Column B at index {index}")
                continue

            # Add valid pair with sanitized ID
            valid_pairs.append({
                "id": len(valid_pairs) + 1,
                "column_a": col_a.strip(),
                "column_b": col_b.strip()
            })

        if not valid_pairs:
            raise ValueError("No valid matching pairs found after validation")

        # Update data with cleaned pairs
        data["pairs"] = valid_pairs
        
        # Ensure title exists
        if "title" not in data or not data["title"]:
            data["title"] = "Match the items"

        return data


# Create singleton instance
question_generator = QuestionGenerator()