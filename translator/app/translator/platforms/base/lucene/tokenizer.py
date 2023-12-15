"""
Uncoder IO Commercial Edition License
-----------------------------------------------------------------
Copyright (c) 2023 SOC Prime, Inc.

This file is part of the Uncoder IO Commercial Edition ("CE") and is
licensed under the Uncoder IO Non-Commercial License (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://github.com/UncoderIO/UncoderIO/blob/main/LICENSE

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-----------------------------------------------------------------
"""
import re

from typing import Tuple, Union, List, Any

from app.translator.core.exceptions.parser import TokenizerGeneralException
from app.translator.core.mixins.logic import ANDLogicOperatorMixin
from app.translator.core.models.field import Keyword, Field
from app.translator.core.models.identifier import Identifier
from app.translator.core.tokenizer import QueryTokenizer
from app.translator.core.custom_types.tokens import OperatorType
from app.translator.tools.utils import get_match_group


class LuceneTokenizer(QueryTokenizer, ANDLogicOperatorMixin):
    single_value_operators_map = {
        ":>": OperatorType.GT,
        ":<": OperatorType.LT,
        ":": OperatorType.EQ
    }
    multi_value_operators_map = {
        ":": OperatorType.EQ
    }

    field_pattern = r"(?P<field_name>[a-zA-Z\.\-_]+)"
    match_operator_pattern = r"(?:___field___\s*(?P<match_operator>:\[\*\sTO|:\[|:<|:>|:))\s*"
    _num_value_pattern = r"\d+(?:\.\d+)*"
    num_value_pattern = fr"(?P<num_value>{_num_value_pattern})\s*"
    double_quotes_value_pattern = r'"(?P<d_q_value>(?:[:a-zA-Z\*0-9=+%#\-_/,\'\.$&^@!\(\)\{\}\s]|\\\"|\\)*)"\s*'
    no_quotes_value_pattern = r"(?P<n_q_value>(?:[a-zA-Z\*0-9=%#_/,\'\.$@]|\\\"|\\\\)+)\s*"
    re_value_pattern = r"/(?P<re_value>[:a-zA-Z\*0-9=+%#\\\-_\,\"\'\.$&^@!\(\)\{\}\[\]\s?]+)/\s*"
    gte_value_pattern = fr"\[\s*(?P<gte_value>{_num_value_pattern})\s+TO\s+\*\s*\]"
    lte_value_pattern = fr"\[\s*\*\s+TO\s+(?P<lte_value>{_num_value_pattern})\s*\]"
    range_value_pattern = fr"{gte_value_pattern}|{lte_value_pattern}"
    _value_pattern = fr"{num_value_pattern}|{re_value_pattern}|{no_quotes_value_pattern}|{double_quotes_value_pattern}|{range_value_pattern}"
    keyword_pattern = r"(?P<n_q_value>(?:[a-zA-Z\*0-9=%#_/,\'\.$@]|\\\"|\\\(|\\\)|\\\[|\\\]|\\\{|\\\}|\\\:|\\)+)(?:\s+|\)|$)"

    multi_value_pattern = r"""\((?P<value>[:a-zA-Z\"\*0-9=+%#\-_\/\\'\,.&^@!\(\[\]\s]+)\)"""
    multi_value_check_pattern = r"___field___\s*___operator___\s*\("

    wildcard_symbol = "*"

    @staticmethod
    def create_field(field_name: str, operator: Identifier, value: Union[str, List]) -> Field:
        field_name = field_name.replace(".text", "")
        field_name = field_name.replace(".keyword", "")
        return Field(operator=operator, value=value, source_name=field_name)

    @staticmethod
    def clean_quotes(value: Union[str, int]):
        if isinstance(value, str):
            return value.strip('"') if value.startswith('"') and value.endswith('"') else value
        return value

    def get_operator_and_value(self, match: re.Match, operator: str = OperatorType.EQ) -> Tuple[str, Any]:
        if (num_value := get_match_group(match, group_name='num_value')) is not None:
            return operator, num_value

        elif (re_value := get_match_group(match, group_name='re_value')) is not None:
            return OperatorType.REGEX, re_value

        elif (n_q_value := get_match_group(match, group_name='n_q_value')) is not None:
            return operator, n_q_value

        elif (d_q_value := get_match_group(match, group_name='d_q_value')) is not None:
            return operator, d_q_value

        elif (gte_value := get_match_group(match, group_name='gte_value')) is not None:
            return OperatorType.GTE, gte_value

        elif (lte_value := get_match_group(match, group_name='lte_value')) is not None:
            return OperatorType.LTE, lte_value

        return super().get_operator_and_value(match, operator)

    def search_value(self, query: str, operator: str, field_name: str) -> Tuple[str, str, Union[str, List[str]]]:
        check_pattern = self.multi_value_check_pattern
        check_regex = check_pattern.replace('___field___', field_name).replace('___operator___', operator)
        if re.match(check_regex, query):
            value_pattern = self.multi_value_pattern
            is_multi = True
        else:
            value_pattern = self.value_pattern
            is_multi = False

        field_value_pattern = self.get_field_value_pattern(operator, field_name)
        field_value_pattern = field_value_pattern.replace("___value___", value_pattern)
        field_value_regex = re.compile(field_value_pattern, re.IGNORECASE)
        field_value_search = re.search(field_value_regex, query)
        if field_value_search is None:
            raise TokenizerGeneralException(error=f"Value couldn't be found in query part: {query}")

        operator, value = self.get_operator_and_value(field_value_search, self.map_operator(operator))
        value = [self.clean_quotes(v) for v in re.split(r"\s+OR\s+", value)] if is_multi else value
        pos = field_value_search.end()
        return query[pos:], operator, value

    def search_keyword(self, query: str) -> Tuple[Keyword, str]:
        keyword_search = re.search(self.keyword_pattern, query)
        _, value = self.get_operator_and_value(keyword_search)
        value = value.strip(self.wildcard_symbol)
        keyword = Keyword(value=value)
        pos = keyword_search.end() - 1
        return keyword, query[pos:]

    def _match_field_value(self, query: str, white_space_pattern: str = r"\s*") -> bool:
        range_value_pattern = f"(?:{self.gte_value_pattern}|{self.lte_value_pattern})"
        range_pattern = fr"{self.field_pattern}{white_space_pattern}:\s*{range_value_pattern}"
        if re.match(range_pattern, query, re.IGNORECASE):
            return True

        return super()._match_field_value(query, white_space_pattern=white_space_pattern)

    def tokenize(self, query: str) -> List[Union[Field, Keyword, Identifier]]:
        tokens = super().tokenize(query=query)
        return self.add_and_token_if_missed(tokens=tokens)
