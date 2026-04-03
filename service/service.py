import os
import pandas as pd

class DataService:
    def __init__(self, data_path: str):
        self.data_path = data_path


    def load_df(self) -> pd.DataFrame:
        if not os.path.exists(self.data_path):
            raise FileNotFoundError(
                
                f"Data file not found: {self.data_path}. "
                f"Place 'data.xls', 'data.xlsx' or 'data.csv' in the project folder or set EXCEL_PATH. "
            )
        
        extension = os.path.splitext(self.data_path)[1].lower()

        if extension == '.csv':
            return pd.read_csv(self.data_path)
        
        if extension in ['.xls', '.xlsx']:
            return pd.read_excel(self.data_path)
        
        raise ValueError(
            f"Unsupported file extension {extension}. Please provide provide file with .csv, .xls, .xlsx file extension only."
        )
    
    def pick_columns(self, df: pd.DataFrame):
        num_column = df.select_dtypes(include='number').columns.to_list()
        y_column = num_column[0] 

        
        x_column = None
        for c in df.columns:
            if str(c).strip().lower() == "name":
                x_column = c
                break

            if x_column is None:
                nonnum = [c for c in df.columns if c not in num_column]
                x_column = nonnum[0] if nonnum else None

        return x_column, y_column
    