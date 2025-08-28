"""Template implementations for different data formats."""

import json
from datetime import date, datetime
from typing import Self

import jsonpath_ng
import pandas as pd
import sqlalchemy
from sqlalchemy import Column, MetaData, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql.sqltypes import Boolean, Date, DateTime, Integer, Numeric, String

from .base import Node, Template
from .utils import JSONPathFunctions


class CSVTemplate(Template):
    """Template for CSV data format."""
    
    def __init__(self):
        pass
    
    def create_template(self) -> str:
        """CSV doesn't need a template structure."""
        return "No real CSV template is created as we can just dump the dataframe to csv"
    
    def fill_data(self, data: pd.DataFrame, source_name: str) -> str:
        """Fill template with DataFrame data."""
        return data.to_csv(index=False)
    
    @property
    def columns_decoded(self) -> bool:
        """CSV columns are already decoded."""
        return True


class RDBTemplate(Template):
    """Template for relational database format."""
    
    def __init__(self, db_url):
        self.db_url = db_url

    def create_engine(self):
        """Create SQLAlchemy engine."""
        return sqlalchemy.create_engine(self.db_url)

    def create_template(self) -> str:
        """RDB template structure is determined by database schema."""
        return "RDB template: structure will be determined by the database schema"

    def fill_data(self, data: pd.DataFrame, table_name: str) -> str:
        """Fill template with data and create SQL statements."""
        engine = self.create_engine()
        table = self._get_sqla_table(data, table_name)
        
        # Convert data types to match schema before creating insert statement
        data = data.copy()
        for col in table.columns:
            if isinstance(col.type, String):
                data[col.name] = data[col.name].map(lambda x: str(x) if x is not None else None)
        
        insert_stmt = postgresql.insert(table).values(data.to_dict(orient='records'))
        
        if data.empty:
            # Create only table structure if DataFrame is empty
            with engine.begin() as connection:
                inspector = sqlalchemy.inspect(engine)
                if not inspector.has_table(table_name):
                    table.create(connection)
            return str(CreateTable(table).compile(engine)) 

        if not self._is_sql_query(table_name):
            with engine.begin() as connection:
                inspector = sqlalchemy.inspect(engine)
                if inspector.has_table(table_name):
                    existing_columns = inspector.get_columns(table_name)
                    existing_column_names = set(col['name'] for col in existing_columns)
                    new_column_names = set(col.name for col in table.columns)

                    # Add missing columns
                    for col in table.columns:
                        if col.name not in existing_column_names:
                            connection.execute(sqlalchemy.text(f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {col.type}'))

                    # Remove extra columns
                    for col_name in existing_column_names - new_column_names:
                        connection.execute(sqlalchemy.text(f'ALTER TABLE "{table_name}" DROP COLUMN "{col_name}"'))

                    # Update column types if necessary
                    for col in table.columns:
                        existing_col = next((c for c in existing_columns if c['name'] == col.name), None)
                        if existing_col and not isinstance(existing_col['type'], col.type.__class__):
                            connection.execute(sqlalchemy.text(f'ALTER TABLE "{table_name}" ALTER COLUMN "{col.name}" TYPE {col.type}'))
                else:
                    # Create table if it doesn't exist
                    table.create(connection)

                # Generate INSERT statements
                connection.execute(insert_stmt)

        # Generate full query for logging purposes
        create_table_query = str(CreateTable(table).compile(engine))
        insert_query = str(insert_stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True}
        ))
        full_query = f"{create_table_query};{insert_query};"

        engine.dispose()
        return full_query

    def _is_sql_query(self, table_name: str) -> bool:
        """Check if table_name contains SQL keywords."""
        sql_keywords = ['SELECT', 'FROM', 'WHERE', 'JOIN', 'GROUP BY', 'ORDER BY']
        return any(keyword in table_name.upper() for keyword in sql_keywords)

    def _get_sqla_table(self, df: pd.DataFrame, table_name: str):
        """Create SQLAlchemy table from DataFrame."""
        metadata = MetaData()
        columns = []
        
        for column_name, dtype in df.dtypes.items():
            # Check if column contains mixed types by examining actual values
            column_values = df[column_name].dropna()
            has_strings = any(isinstance(val, str) for val in column_values)
            has_numbers = any(isinstance(val, (int, float)) for val in column_values)
            
            # If column has mixed strings and numbers, or contains strings, use String type
            if has_strings or (has_strings and has_numbers):
                col_type = String()
            elif "int" in str(dtype):
                col_type = Integer()
            elif "float" in str(dtype):
                col_type = Numeric()
            elif "bool" in str(dtype):
                col_type = Boolean()
            elif "datetime" in str(dtype):
                col_type = DateTime()
            elif "date" in str(dtype):
                col_type = Date()
            else:
                col_type = String()
            
            columns.append(Column(column_name, col_type))
        
        return Table(table_name, metadata, *columns)

    @property
    def columns_decoded(self) -> bool:
        """RDB columns are decoded."""
        return True


class JSONTemplate(Template):
    """Template for JSON data format."""
    
    def __init__(self):
        self.paths: list[jsonpath_ng.JSONPath] = []
    
    @property
    def columns_decoded(self) -> bool:
        """JSON columns need decoding."""
        return False
    
    @property
    def root(self) -> Node:
        """Create the root node from paths."""
        if len(self.paths) == 0:
            return Root()
            
        # Find a path connected to the root
        root_path = None
        for path in self.paths:
            top_steps = JSONPathFunctions.list_path_steps(path)
            if isinstance(top_steps[0], jsonpath_ng.Root):
                root_path = path
                break
                
        if root_path is None:
            raise ValueError("No root path found")
            
        root = self._create_node_tree(JSONPathFunctions.list_path_steps(root_path))
        
        for path in self.paths:
            # Merge paths into the tree
            node = self._create_node_tree(JSONPathFunctions.list_path_steps(path))
            self._merge_node_trees(root, node)
            
        return root
    
    def add_path(self, jsonpath: jsonpath_ng.JSONPath | str) -> bool:
        """Add a full path to the template."""
        if isinstance(jsonpath, str):
            jsonpath = jsonpath_ng.parse(jsonpath)
        if jsonpath in self.paths:
            return False
        self.paths.append(jsonpath)
        return True
    
    def _create_node_tree(self, steps: list[jsonpath_ng.JSONPath]) -> Node:
        """Create a tree of nodes from path steps."""
        if len(steps) == 0:
            return None
        if len(steps) == 1:
            return Object(values=[steps[0].fields[0]])
            
        root_step = steps[0]
        if isinstance(root_step, jsonpath_ng.Root):
            root = Root()
        elif isinstance(root_step, jsonpath_ng.Fields):
            root = Object()
            key = root_step.fields[0]
        elif isinstance(root_step, jsonpath_ng.Slice):
            root = Array()
        else:
            raise ValueError(f"Unsupported step type: {type(root_step)}")
            
        current = root
        for step in steps[1:-1]:
            if isinstance(step, jsonpath_ng.Fields):
                next_node = Object()
                key = step.fields[0]
            elif isinstance(step, jsonpath_ng.Slice):
                next_node = Array()
            else:
                raise ValueError(f"Unsupported step type: {type(step)}")
                
            if isinstance(current, Object):
                current.add_child(key, next_node)
            elif isinstance(current, Array):
                current.content = next_node
            elif isinstance(current, Root):
                current.child = next_node
                
            current = next_node
            
        leaf = Object(values=[steps[-1].fields[0]])
        if isinstance(current, Object):
            current.add_child(key, leaf)
        elif isinstance(current, Array):
            current.content = leaf
            
        return root
    
    def _merge_node_trees(self, base: Node, other: Node):
        """Merge two node trees."""
        if isinstance(base, Object):
            if isinstance(other, Object):
                for key, child in other.children.items():
                    if key in base.children.keys():
                        self._merge_node_trees(base.children[key], child)
                    else:
                        base.children[key] = child
                for value in other.values:
                    if value not in base.values:
                        base.values.append(value)
            else:
                raise ValueError("Cannot merge Object with non-Object")
        elif isinstance(base, Array):
            if isinstance(other, Array):
                self._merge_node_trees(base.content, other.content)
            else:
                raise ValueError("Cannot merge Array with non-Array")
        elif isinstance(base, Root):
            if isinstance(other, Root):
                self._merge_node_trees(base.child, other.child)
            else:
                raise ValueError("Cannot merge Root with non-Root")
                
    def create_template(self) -> str:
        """Create a template string."""
        return self.root.to_template()
    
    def fill_data(self, data: pd.DataFrame, source_name: str) -> str:
        """Fill template with data."""
        return self.root.fill(data)
    
    def __str__(self):
        return f"JSONTemplate({self.paths})"


# JSON Template Node implementations
class Object(Node):
    """JSON object node."""
    
    def __init__(self, children: dict[str, Node] = None, values: list[str] = None):
        self._parent_path = ""
        self.children = children or {}
        self.values = values or []
    
    def add_child(self, key: str, child: Node):
        """Add a child node."""
        self.children[key] = child
        child.parent_path = self.path + "." + key
    
    @property
    def path(self) -> str:
        return self.parent_path 
    
    @property
    def parent_path(self) -> str:
        return self._parent_path
    
    @parent_path.setter
    def parent_path(self, value: str):
        self._parent_path = value
    
    def find(self, key: str) -> Self | None:
        """Find a child by key."""
        if key in self.children.keys():
            return self.children[key]
        for child in self.children.values():
            result = child.find(key)
            if result is not None:
                return result
        return None
            
    def to_template(self) -> str:
        """Convert to template string."""
        child_strings = [f'"{key}": {child.to_template()}' for key, child in self.children.items()]
        value_strings = [f'"{value}": "${value}"' for value in self.values]
        return "{" + ", ".join(child_strings + value_strings) + "}"
            
    def fill(self, data: pd.DataFrame) -> str:
        """Fill with actual data."""
        paths = [JSONPathFunctions.normalize_json_path(f"{self.path}.['{value}']") for value in self.values]
        filled_values = [f'"{value}": "{data[path].iloc[0]}"' for value, path in zip(self.values, paths)]
        filled_children = [f'"{key}": {child.fill(data)}' for key, child in self.children.items()]
        return "{" + ", ".join(filled_values + filled_children) + "}"
    
    def get_slice_columns(self) -> list[str]:
        """Get columns for slicing."""
        columns = [JSONPathFunctions.normalize_json_path(f"{self.path}.['{value}']") for value in self.values]
        for child in self.children.values():
            if isinstance(child, Object):
                columns.extend(child.get_slice_columns())
        return columns


class Array(Node):
    """JSON array node."""
    
    def __init__(self, content: Node = None):
        self._parent_path = ""
        self._content = None
        if content is not None:
            self.content = content
    
    @property
    def path(self) -> str:
        return self.parent_path + "[*]"
    
    @property
    def parent_path(self) -> str:
        return self._parent_path
    
    @parent_path.setter
    def parent_path(self, value: str):
        self._parent_path = value
    
    @property
    def content(self) -> Node:
        return self._content
    
    @content.setter
    def content(self, value: Node):
        self._content = value
        if self._content:
            self._content.parent_path = self.path

    def find(self, key: str) -> Self | None:
        """Find in content."""
        if self._content:
            return self._content.find(key)
        return None

    def to_template(self) -> str:
        """Convert to template string."""
        if self.content:
            return "[" + self.content.to_template() + "]"
        return "[]"
    
    def fill(self, data: pd.DataFrame) -> str:
        """Fill with actual data."""
        if isinstance(self.content, Object):
            columns = self.content.get_slice_columns()
            grouped_data = data.groupby(columns, dropna=False)
            content_lines = []
            
            for _, group in grouped_data:
                if len(group) == 0:
                    continue
                content_lines.append(self.content.fill(group))
            
            joined_content = ", ".join(content_lines)
            return "[" + joined_content + "]"
        
        if self.content:
            return "[" + self.content.fill(data) + "]"
        return "[]"


class Root(Node):
    """JSON root node."""
    
    def __init__(self, child: Node = None):
        self._child = None
        if child is not None:
            self.child = child
            
    @property
    def child(self) -> Node:
        return self._child
    
    @child.setter
    def child(self, value: Node):
        self._child = value
        if self._child:
            self._child.parent_path = "$"
        
    @property
    def path(self) -> str:
        return "$"
    
    @property
    def parent_path(self) -> str:
        return ""

    def find(self, key: str) -> Self | None:
        """Find in child."""
        if self.child is None:
            return None
        return self.child.find(key)
    
    def to_template(self) -> str:
        """Convert to template string."""
        if self.child is None:
            return "{}"
        return self.child.to_template()
    
    def fill(self, data: pd.DataFrame) -> str:
        """Fill with actual data."""
        if self.child is None:
            return "{}"
        return self.child.fill(data)