import abc
import json
import os
from typing import Optional

import yaml
from simple_ddl_parser import DDLParser

from biochatter._misc import ensure_iterable, sentencecase_to_pascalcase
from biochatter.llm_connect import GptConversation


class PromptEngine:
    """
    Using the schema definition of a data structure, allow
    the generation of prompts for a large language model, informing it of
    the schema constituents and their properties, to enable the
    parameterisation of function calls to the data structure.

    Currently implemented for knowledge graphs (from BioCypher) and relational databases.
    """

    def __init__(
        self,
        schema_config_or_info_path: Optional[str] = None,
        schema_config_or_info_dict: Optional[dict] = None,
        model_name: str = "gpt-3.5-turbo",
    ):
        self.model_name = model_name
        self.question = ""
        self.entities = {}
        self.relationships = {}
        self.properties = {}
        self.selected_entities = []
        self.selected_relationships = []  # used in property selection
        self.selected_relationship_labels = {}  # copy to deal with labels that
        # are not the same as the relationship name, used in query generation
        # dictionary to also include source and target types
        self.rel_directions = {}

        if not schema_config_or_info_path and not schema_config_or_info_dict:
            raise ValueError(
                "Please provide the schema configuration or schema info as a "
                "path to a file or as a dictionary."
            )

        if schema_config_or_info_path and schema_config_or_info_dict:
            raise ValueError(
                "Please provide the schema configuration or schema info as a "
                "path to a file or as a dictionary, not both."
            )

        self.load_schema(schema_config_or_info_path, schema_config_or_info_dict)

    @abc.abstractmethod
    def load_schema(
        self, schema_config_or_info_path, schema_config_or_info_dict
    ):
        pass

    def generate_query(self, question: str, query_language: str) -> str:
        """
        Wrap entity and property selection and query generation; return the
        generated query.

        Args:
            question: A user's question.

            query_language: The language of the query to generate.

        Returns:
            A database query that could answer the user's question.
        """

        success1 = self._select_entities(question)
        if not success1:
            raise ValueError(
                "Entity selection failed. Please try again with a different "
                "question."
            )
        success2 = self._select_relationships()
        if not success2:
            raise ValueError(
                "Relationship selection failed. Please try again with a "
                "different question."
            )
        success3 = self._select_properties()
        if not success3:
            raise ValueError(
                "Property selection failed. Please try again with a different "
                "question."
            )

        return self._generate_query(
            question=question,
            entities=self.selected_entities,
            relationships=self.selected_relationship_labels,
            properties=self.selected_properties,
            query_language=query_language,
        )

    def _generate_query(
        self,
        question: str,
        entities: list,
        relationships: dict,
        properties: dict,
        query_language: str,
    ):
        """
        Generate a query in the specified query language that answers the user's
        question.

        Args:
            question: A user's question.

            entities: A list of entities that are relevant to the question.

            relationships: A list of relationships that are relevant to the
                question.

            properties: A dictionary of properties that are relevant to the
                question.

            query_language: The language of the query to generate.
        """
        msg = (
            f"Generate a database query in {query_language} that answers "
            f"the user's question. "
            f"You can use the following entities: {entities}, "
            f"relationships: {list(relationships.keys())}, and "
            f"properties: {properties}. "
        )

        for relationship, values in relationships.items():
            self._expand_pairs(relationship, values)

        if self.rel_directions:
            msg += "Given the following valid combinations of source, relationship, and target: "
            for key, value in self.rel_directions.items():
                for pair in value:
                    msg += f"'(:{pair[0]})-(:{key})->(:{pair[1]})', "
            msg += f"generate a {query_language} query using one of these combinations. "

        msg += "Only return the query, without any additional text."

        conversation = GptConversation(
            model_name=self.model_name,
            prompts={},
            correct=False,
        )

        conversation.set_api_key(
            api_key=os.getenv("OPENAI_API_KEY"), user="query_generator"
        )

        conversation.append_system_message(msg)

        out_msg, token_usage, correction = conversation.query(question)

        return out_msg


class BioCypherPromptEngine(PromptEngine):
    def load_schema(
        self,
        schema_config_or_info_path: Optional[str] = None,
        schema_config_or_info_dict: Optional[dict] = None,
    ):
        """

        Given a biocypher schema configuration, extract the entities and
        relationships, and for each extract their mode of representation (node
        or edge), properties, and identifier namespace.

        Args:
            schema_config_or_info_path: Path to a biocypher schema configuration
                file or the extended schema information output generated by
                BioCypher's `write_schema_info` function (preferred).

            schema_config_or_info_dict: A dictionary containing the schema
                configuration file or the extended schema information output
                generated by BioCypher's `write_schema_info` function
                (preferred).

        Todo:
            inject conversation directly instead of specifying model name?
        """
        if schema_config_or_info_path:
            # read the schema configuration
            with open(schema_config_or_info_path, "r") as f:
                schema_config = yaml.safe_load(f)
        elif schema_config_or_info_dict:
            schema_config = schema_config_or_info_dict

        # check whether it is the original schema config or the output of
        # biocypher info
        is_schema_info = schema_config.get("is_schema_info", False)

        # extract the entities and relationships: each top level key that has
        # a 'represented_as' key
        if not is_schema_info:
            for key, value in schema_config.items():
                # hacky, better with biocypher output
                name_indicates_relationship = (
                    "interaction" in key.lower() or "association" in key.lower()
                )
                if "represented_as" in value:
                    if (
                        value["represented_as"] == "node"
                        and not name_indicates_relationship
                    ):
                        self.entities[sentencecase_to_pascalcase(key)] = value
                    elif (
                        value["represented_as"] == "node"
                        and name_indicates_relationship
                    ):
                        self.relationships[
                            sentencecase_to_pascalcase(key)
                        ] = value
                    elif value["represented_as"] == "edge":
                        self.relationships[
                            sentencecase_to_pascalcase(key)
                        ] = value
        else:
            for key, value in schema_config.items():
                if not isinstance(value, dict):
                    continue
                if value.get("present_in_knowledge_graph", None) == False:
                    continue
                if value.get("is_relationship", None) == False:
                    self.entities[sentencecase_to_pascalcase(key)] = value
                elif value.get("is_relationship", None) == True:
                    value = self._capitalise_source_and_target(value)
                    self.relationships[sentencecase_to_pascalcase(key)] = value

    def _capitalise_source_and_target(self, relationship: dict) -> dict:
        """
        Make sources and targets PascalCase to match the entities. Sources and
        targets can be strings or lists of strings.
        """
        if "source" in relationship:
            if isinstance(relationship["source"], str):
                relationship["source"] = sentencecase_to_pascalcase(
                    relationship["source"]
                )
            elif isinstance(relationship["source"], list):
                relationship["source"] = [
                    sentencecase_to_pascalcase(s)
                    for s in relationship["source"]
                ]
        if "target" in relationship:
            if isinstance(relationship["target"], str):
                relationship["target"] = sentencecase_to_pascalcase(
                    relationship["target"]
                )
            elif isinstance(relationship["target"], list):
                relationship["target"] = [
                    sentencecase_to_pascalcase(t)
                    for t in relationship["target"]
                ]
        return relationship

    def _select_entities(self, question: str) -> bool:
        """

        Given a question, select the entities that are relevant to the question
        and store them in `selected_entities` and `selected_relationships`. Use
        LLM conversation to do this.

        Args:
            question: A user's question.

        Returns:
            True if at least one entity was selected, False otherwise.

        """

        self.question = question

        conversation = GptConversation(
            model_name=self.model_name,
            prompts={},
            correct=False,
        )

        conversation.set_api_key(
            api_key=os.getenv("OPENAI_API_KEY"), user="entity_selector"
        )

        conversation.append_system_message(
            (
                "You have access to a knowledge graph that contains "
                f"these entities: {', '.join(self.entities)}. Your task is "
                "to select the ones that are relevant to the user's question "
                "for subsequent use in a query. Only return the entities, "
                "comma-separated, without any additional text. "
            )
        )

        msg, token_usage, correction = conversation.query(question)

        result = msg.split(",") if msg else []
        # TODO: do we go back and retry if no entities were selected? or ask for
        # a reason? offer visual selection of entities and relationships by the
        # user?

        if result:
            for entity in result:
                entity = entity.strip()
                if entity in self.entities:
                    self.selected_entities.append(entity)

        return bool(result)

    def _select_relationships(self) -> bool:
        """
        Given a question and the preselected entities, select relationships for
        the query.

        Args:
            question: A user's question.

            entities: A list of entities that are relevant to the question.

        Returns:
            True if at least one relationship was selected, False otherwise.

        Todo:
            Now we have the problem that we discard all relationships that do
            not have a source and target, if at least one relationship has a
            source and target. At least communicate this all-or-nothing
            behaviour to the user.
        """

        if not self.question:
            raise ValueError(
                "No question found. Please make sure to run entity selection "
                "first."
            )

        if not self.selected_entities:
            raise ValueError(
                "No entities found. Please run the entity selection step first."
            )

        rels = {}
        source_and_target_present = False
        for key, value in self.relationships.items():
            if "source" in value and "target" in value:
                # if source or target is a list, expand to single pairs
                source = ensure_iterable(value["source"])
                target = ensure_iterable(value["target"])
                pairs = []
                for s in source:
                    for t in target:
                        pairs.append((s, t))
                rels[key] = pairs
                source_and_target_present = True
            else:
                rels[key] = {}

        # prioritise relationships that have source and target, and discard
        # relationships that do not have both source and target, if at least one
        # relationship has both source and target. keep relationships that have
        # either source or target, if none of the relationships have both source
        # and target.

        if source_and_target_present:
            # First, separate the relationships into two groups: those with both
            # source and target in the selected entities, and those with either
            # source or target but not both.

            rels_with_both = {}
            rels_with_either = {}
            for key, value in rels.items():
                for pair in value:
                    if pair[0] in self.selected_entities:
                        if pair[1] in self.selected_entities:
                            rels_with_both[key] = value
                        else:
                            rels_with_either[key] = value
                    elif pair[1] in self.selected_entities:
                        rels_with_either[key] = value

            # If there are any relationships with both source and target,
            # discard the others.

            if rels_with_both:
                rels = rels_with_both
            else:
                rels = rels_with_either

            selected_rels = []
            for key, value in rels.items():
                if not value:
                    continue

                for pair in value:
                    if (
                        pair[0] in self.selected_entities
                        or pair[1] in self.selected_entities
                    ):
                        selected_rels.append((key, pair))

            rels = json.dumps(selected_rels)
        else:
            rels = json.dumps(self.relationships)

        conversation = GptConversation(
            model_name=self.model_name,
            prompts={},
            correct=False,
        )

        conversation.set_api_key(
            api_key=os.getenv("OPENAI_API_KEY"), user="entity_selector"
        )

        msg = (
            "You have access to a knowledge graph that contains "
            f"these entities: {', '.join(self.selected_entities)}. "
            "Your task is to select the relationships that are relevant "
            "to the user's question for subsequent use in a query. Only "
            "return the relationships without their sources or targets, "
            "comma-separated, and without any additional text. Here are the "
            "possible relationships and their source and target entities: "
            f"{rels}."
        )

        conversation.append_system_message(msg)

        res, token_usage, correction = conversation.query(self.question)

        result = res.split(",") if msg else []

        if result:
            for relationship in result:
                relationship = relationship.strip()
                if relationship in self.relationships:
                    self.selected_relationships.append(relationship)
                    rel_dict = self.relationships[relationship]
                    label = rel_dict.get("label_as_edge", relationship)
                    if "source" in rel_dict and "target" in rel_dict:
                        self.selected_relationship_labels[label] = {
                            "source": rel_dict["source"],
                            "target": rel_dict["target"],
                        }
                    else:
                        self.selected_relationship_labels[label] = {
                            "source": None,
                            "target": None,
                        }

        # if we selected relationships that have either source or target which
        # is not in the selected entities, we add those entities to the selected
        # entities.

        if self.selected_relationship_labels:
            for key, value in self.selected_relationship_labels.items():
                sources = ensure_iterable(value["source"])
                targets = ensure_iterable(value["target"])
                for source in sources:
                    if source not in self.selected_entities:
                        self.selected_entities.append(source)
                for target in targets:
                    if target not in self.selected_entities:
                        self.selected_entities.append(target)

        return bool(result)

    def _select_properties(
        self,
    ):
        """

        Given a question (optionally provided, but in the standard use case
        reused from the entity selection step) and the selected entities, select
        the properties that are relevant to the question and store them in
        the dictionary `selected_properties`.

        Returns:
            True if at least one property was selected, False otherwise.

        """

        if not self.question:
            raise ValueError(
                "No question found. Please make sure to run entity and "
                "relationship selection first."
            )

        if not self.selected_entities and not self.selected_relationships:
            raise ValueError(
                "No entities or relationships provided, and none available "
                "from entity selection step. Please provide "
                "entities/relationships or run the entity selection "
                "(`select_entities()`) step first."
            )

        e_props = {}
        for entity in self.selected_entities:
            if self.entities[entity].get("properties"):
                e_props[entity] = list(
                    self.entities[entity]["properties"].keys()
                )

        r_props = {}
        for relationship in self.selected_relationships:
            if self.relationships[relationship].get("properties"):
                r_props[relationship] = list(
                    self.relationships[relationship]["properties"].keys()
                )

        msg = (
            "You have access to a knowledge graph that contains entities and "
            "relationships. They have the following properties. Entities:"
            f"{e_props}, Relationships: {r_props}. "
            "Your task is to select the properties that are relevant to the "
            "user's question for subsequent use in a query. Only return the "
            "entities and relationships with their relevant properties in JSON "
            "format, without any additional text. Return the "
            "entities/relationships as top-level dictionary keys, and their "
            "properties as dictionary values. "
            "Do not return properties that are not relevant to the question."
        )

        conversation = GptConversation(
            model_name=self.model_name,
            prompts={},
            correct=False,
        )

        conversation.set_api_key(
            api_key=os.getenv("OPENAI_API_KEY"), user="property_selector"
        )

        conversation.append_system_message(msg)

        msg, token_usage, correction = conversation.query(self.question)

        self.selected_properties = json.loads(msg) if msg else {}

        return bool(self.selected_properties)

    def _expand_pairs(self, relationship, values):
        if not self.rel_directions.get(relationship):
            self.rel_directions[relationship] = []
        if isinstance(values["source"], list):
            for source in values["source"]:
                if isinstance(values["target"], list):
                    for target in values["target"]:
                        self.rel_directions[relationship].append(
                            (source, target)
                        )
                else:
                    self.rel_directions[relationship].append(
                        (source, values["target"])
                    )
        elif isinstance(values["target"], list):
            for target in values["target"]:
                self.rel_directions[relationship].append(
                    (values["source"], target)
                )
        else:
            self.rel_directions[relationship].append(
                (values["source"], values["target"])
            )


class SQLPromptEngine(PromptEngine):
    def load_schema(
        self,
        schema_config_or_info_path: Optional[str] = None,
        schema_config_or_info_dict: Optional[dict] = None,
    ):
        """
        Given a relational database schema (SQL), extract the entities,
        relationships, and properties.

        Args:
            schema_config_or_info_path: Path to a SQL file.

            schema_config_or_info_dict: A json dictionary containing the schema
                definition as generated by simple-ddl-parser.
        """
        if schema_config_or_info_path:
            # read the schema SQL definition
            with open(schema_config_or_info_path, "r") as f:
                ddl = f.read()
                schema_config = DDLParser(ddl).run(group_by_type=True)
        elif schema_config_or_info_dict:
            schema_config = schema_config_or_info_dict

        # extract the entities from the tables
        for table in schema_config["tables"]:
            name_indicates_relationship = (
                "to" in table["table_name"].split("_")
                or "interaction" in table["table_name"]
            )
            if not name_indicates_relationship:
                self.entities[table["table_name"].lower()] = {
                    "properties": [
                        column["name"].lower() for column in table["columns"]
                    ]
                    # TODO can add other information here or take the complete dictionary from DDL parser
                }
            else:
                self.relationships[table["table_name"].lower()] = {
                    "properties": [
                        column["name"].lower() for column in table["columns"]
                    ]
                    # TODO can add other information here or take the complete dictionary from DDL parser
                }

        # TODO also get relationships from foreign keys?

        self.properties = {}
        for entity in self.entities:
            if self.entities[entity].get("properties"):
                self.properties[entity] = self.entities[entity]["properties"]

    def _select_entities(self, question: str) -> bool:
        """

        Given a question, select the entities that are relevant to the question
        and store them in `selected_entities` and `selected_relationships`. Use
        LLM conversation to do this.

        Args:
            question: A user's question.

        Returns:
            True if at least one entity was selected, False otherwise.

        """

        self.question = question

        conversation = GptConversation(
            model_name=self.model_name,
            prompts={},
            correct=False,
        )

        conversation.set_api_key(
            api_key=os.getenv("OPENAI_API_KEY"), user="entity_selector"
        )

        conversation.append_system_message(
            (
                "You have access to a relational database that contains "
                f"these tables representing entities: {', '.join(self.entities)}. Your task is "
                "to select the ones that are relevant to the user's question "
                "for subsequent use in a query. Only return the entities, "
                "comma-separated, without any additional text. "
            )
        )

        msg, token_usage, correction = conversation.query(question)

        result = msg.split(",") if msg else []
        # TODO: do we go back and retry if no entities were selected? or ask for
        # a reason? offer visual selection of entities and relationships by the
        # user?

        if result:
            for entity in result:
                entity = entity.strip()
                if entity in self.entities:
                    self.selected_entities.append(entity)

        return bool(result)

    def _select_properties(
        self,
    ):
        """

        Given a question (optionally provided, but in the standard use case
        reused from the entity selection step) and the selected entities, select
        the properties that are relevant to the question and store them in
        the dictionary `selected_properties`.

        Returns:
            True if at least one property was selected, False otherwise.

        """

        if not self.question:
            raise ValueError(
                "No question found. Please make sure to run entity and "
                "relationship selection first."
            )

        if not self.selected_entities:
            raise ValueError(
                "No entities provided, and none available "
                "from entity selection step. Please provide "
                "entities or run the entity selection "
                "(`select_entities()`) step first."
            )

        e_props = {}
        for entity in self.selected_entities:
            if self.entities[entity].get("properties"):
                e_props[entity] = self.entities[entity]["properties"]

        r_props = {}
        for relationship in self.selected_relationships:
            if self.relationships[relationship].get("properties"):
                r_props[relationship] = self.relationships[relationship][
                    "properties"
                ]

        msg = (
            "You have access to a knowledge graph that contains entities and "
            "relationships. They have the following properties. Entities: "
            f"{e_props}, Relationships: {r_props}. "
            "Your task is to select the properties that are relevant to the "
            "user's question for subsequent use in a query. Only return the "
            "entities and relationships with their relevant properties in JSON "
            "format, without any additional text. Return the "
            "entities/relationships as top-level dictionary keys, and their "
            "properties as dictionary values. "
            "Do not return properties that are not relevant to the question."
        )

        conversation = GptConversation(
            model_name=self.model_name,
            prompts={},
            correct=False,
        )

        conversation.set_api_key(
            api_key=os.getenv("OPENAI_API_KEY"), user="property_selector"
        )

        conversation.append_system_message(msg)

        msg, token_usage, correction = conversation.query(self.question)

        self.selected_properties = json.loads(msg) if msg else {}

        return bool(self.selected_properties)

    def _select_relationships(self) -> bool:
        """
        Given a question and the preselected entities, select relationships for
        the query.

        Args:
            question: A user's question.

            entities: A list of entities that are relevant to the question.

        Returns:
            True
        """
        # TODO implement, but not as relevant for relational db;
        #  it's also not essential so this should always return True
        return True
