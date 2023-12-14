import os
from typing import Optional

from biochatter.llm_connect import GptConversation


class BioCypherQueryHandler:
    def __init__(
        self,
        query: str,
        query_lang: str,
        kg_selected: dict,
        question: str,
        kg: dict = None,
        # could be issue if the KG is very large and we pass it to the system message each time... -> optional
        model_name: str = "gpt-3.5-turbo",
    ):
        """
        Args:
            query: A KG query generated by the LLM

            query_language: The language of the query.

            question: A user's question that is answered by the query.

            kg: A dictionary containing the entities, properties, relationships that make up the KG.

            kg_selected: A dictionary with a subset of KG entities, properties and relationships
                that are relevant to the question.
        """
        self.query = query
        self.query_lang = query_lang
        self.question = question
        if kg and self._check_required_kg_keys(kg):
            self.kg = kg
        if self._check_required_kg_keys(kg_selected):
            self.kg_selected = kg_selected
        self.model_name = model_name

    @staticmethod
    def _check_required_kg_keys(kg_dict):
        required_keys = ["entities", "properties", "relationships"]
        # Check if all required keys are present in the input dictionary
        if not all(key in kg_dict for key in required_keys):
            raise ValueError(
                "The KG input dictionary is missing required keys."
            )
        # todo also check that entities and relationships is a dict, properties is a dict of lists
        return True

    def explain_query(self):
        """
        Explain the query - this is called from the ChatGSE frontend IF the query ran successfully
        """
        msg = (
            f"You are an expert in {self.query_lang} and will assist in explaining a query.\n"
            f"The query answers the following user question: '{self.question}'."
            "It will be used to query a knowledge graph that contains (among others)"
            f" the following entities: {self.kg_selected['entities']}, "
            f"relationships: {list(self.kg_selected['relationships'].keys())}, and "
            f"properties: {self.kg_selected['properties']}. "
        )

        msg += "Only return the explanation, without any additional text."

        conversation = GptConversation(
            model_name=self.model_name,
            prompts={},
            correct=False,
        )

        conversation.set_api_key(
            api_key=os.getenv("OPENAI_API_KEY"), user="query_interactor"
        )

        conversation.append_system_message(msg)

        out_msg, token_usage, correction = conversation.query(self.query)

        return out_msg

    def update_query(self, update_request):
        """
        Update the query to reflect a request from the user.
        """
        if not self.kg:
            self.kg = self.kg_selected
        msg = (
            f"You are an expert in {self.query_lang} and will assist in updating a query.\n"
            f"The original query answers the following user question: '{self.question}'."
            f"This is the original query: '{self.query}'."
            f"It will be used to query a knowledge graph that has the following entities: "
            f"{self.kg['entities']}, relationships: {list(self.kg['relationships'].keys())}, and "
            f"properties: {self.kg['properties']}. "
        )

        # TODO is something like this needed?

        # for relationship, values in self.kg_selected['relationships'].items():
        #     self._expand_pairs(relationship, values)
        #
        # if self.rel_directions:
        #     msg += "Given the following valid combinations of source, relationship, and target: "
        #     for key, value in self.rel_directions.items():
        #         for pair in value:
        #             msg += f"'(:{pair[0]})-(:{key})->(:{pair[1]})'."

        msg += (
            "Update the query to reflect the user's request."
            "Only return the updated query, without any additional text."
        )

        conversation = GptConversation(
            model_name=self.model_name,
            prompts={},
            correct=False,
        )

        conversation.set_api_key(
            api_key=os.getenv("OPENAI_API_KEY"), user="query_interactor"
        )

        conversation.append_system_message(msg)

        out_msg, token_usage, correction = conversation.query(update_request)

        return out_msg
