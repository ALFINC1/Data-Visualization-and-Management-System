import os
import io
import csv
import pandas as pd


class DataService:
   

    def __init__(self, data_path: str):
        self.data_path = data_path

        self._cache_df = None
        self._cache_mtime = None

    def load_df(self) -> pd.DataFrame:
        
        self._validate_exists()

        mtime = os.path.getmtime(self.data_path)
        if self._cache_df is not None and self._cache_mtime == mtime:
            return self._cache_df.copy()

        ext = os.path.splitext(self.data_path)[1].lower()

        if ext == ".csv":
            df = self._read_csv_smart(self.data_path)
        elif ext in [".xls", ".xlsx"]:
            df = self._read_excel_smart(self.data_path)
        else:
            raise ValueError(
                f"Unsupported file extension: {ext}. "
                f"Please provide only .csv, .xls, or .xlsx"
            )

        df = self._normalize_columns(df)

        self._cache_df = df.copy()
        self._cache_mtime = mtime

        return df

    def pick_columns(self, df: pd.DataFrame):
        
        if df is None or df.empty:
            return None, None

        num_columns = df.select_dtypes(include="number").columns.to_list()

        y_column = num_columns[0] if len(num_columns) > 0 else None

        preferred_x = ["name", "category", "type", "label", "country", "region"]
        lower_map = {str(c).strip().lower(): c for c in df.columns}

        x_column = None
        for key in preferred_x:
            if key in lower_map:
                x_column = lower_map[key]
                break

        if x_column is None:
            nonnum = [c for c in df.columns if c not in num_columns]
            x_column = nonnum[0] if nonnum else (df.columns[0] if len(df.columns) > 0 else None)

        return x_column, y_column

    def _validate_exists(self):
        if not os.path.exists(self.data_path):
            raise FileNotFoundError(
                f"Data file not found: {self.data_path}. "
                f"Place 'data.xls', 'data.xlsx' or 'data.csv' in the project folder or set DATA_PATH."
            )

    def _read_excel_smart(self, path: str) -> pd.DataFrame:
        
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".xlsx":
                return pd.read_excel(path, engine="openpyxl")
            return pd.read_excel(path)
        except Exception as e:
            raise ValueError(f"Failed to read Excel file: {path}. Error: {e}")

    def _read_csv_smart(self, path: str) -> pd.DataFrame:
        
        encodings = ["utf-8-sig", "utf-8", "latin-1"]
        last_err = None

        for enc in encodings:
            try:
                # Try delimiter sniffing from a sample
                sep = self._sniff_csv_delimiter(path, encoding=enc)

                return pd.read_csv(
                    path,
                    encoding=enc,
                    sep=sep,
                    engine="python",     
                    on_bad_lines="skip", 
                    low_memory=False
                )
            except Exception as e:
                last_err = e

        raise ValueError(f"Failed to read CSV file: {path}. Error: {last_err}")

    def _sniff_csv_delimiter(self, path: str, encoding="utf-8") -> str:
        
        try:
            with open(path, "r", encoding=encoding, errors="ignore") as f:
                sample = f.read(4096)
            if not sample.strip():
                return ","
            dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
            return dialect.delimiter
        except Exception:
            return ","

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        
        cols = []
        seen = {}

        for c in df.columns:
            name = str(c).strip()
            name = name.replace(" ", "_")
            name = name.replace("\n", "_").replace("\t", "_")

            if name == "":
                name = "Column"

            # Ensure unique
            base = name
            if base in seen:
                seen[base] += 1
                name = f"{base}_{seen[base]}"
            else:
                seen[base] = 1

            cols.append(name)

        df = df.copy()
        df.columns = cols
        return df