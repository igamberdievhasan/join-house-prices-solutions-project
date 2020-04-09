import numpy as np
import pandas as pd 
import predict 
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn import preprocessing
import time
from datetime import datetime
import warnings
import os
warnings.filterwarnings('ignore')
# ML libraries
import lightgbm as lgb
import xgboost as xgb
from xgboost import plot_importance, plot_tree
from sklearn.model_selection import RandomizedSearchCV, GridSearchCV
from sklearn import linear_model
from sklearn.metrics import mean_squared_error
le = preprocessing.LabelEncoder()


world_population_dir = os.environ.get("WORLD_POPULATION")





# details adding into columns 
def calculate_trend(df, lag_list, column):
    for lag in lag_list:
        trend_column_lag = "Trend_" + column + "_" + str(lag)
        df[trend_column_lag] = (df[column]-df[column].shift(lag, fill_value=-999))/df[column].shift(lag, fill_value=0)
    return df


def calculate_lag(df, lag_list, column):
    for lag in lag_list:
        column_lag = column + "_" + str(lag)
        df[column_lag] = df[column].shift(lag, fill_value=0)
    return df

def addingColumns(train, test): 
	# Merge train and test, exclude overlap
	dates_overlap = ['2020-03-12','2020-03-13','2020-03-14','2020-03-15','2020-03-16','2020-03-17','2020-03-18',
	                 '2020-03-19','2020-03-20','2020-03-21','2020-03-22','2020-03-23', '2020-03-24']
	train2 = train.loc[~train['Date'].isin(dates_overlap)]
	all_data = pd.concat([train2, test], axis = 0, sort=False)
	# Double check that there are no informed ConfirmedCases and Fatalities after 2020-03-11
	all_data.loc[all_data['Date'] >= '2020-03-12', 'ConfirmedCases'] = np.nan
	all_data.loc[all_data['Date'] >= '2020-03-12', 'Fatalities'] = np.nan
	all_data['Date'] = pd.to_datetime(all_data['Date'])

	# Create date columns
	# le = preprocessing.LabelEncoder()
	all_data['Day_num'] = le.fit_transform(all_data.Date)
	all_data['Day'] = all_data['Date'].dt.day
	all_data['Month'] = all_data['Date'].dt.month
	all_data['Year'] = all_data['Date'].dt.year

	# Fill null values given that we merged train-test datasets
	all_data['Province/State'].fillna("None", inplace=True)
	all_data['ConfirmedCases'].fillna(0, inplace=True)
	all_data['Fatalities'].fillna(0, inplace=True)
	all_data['Id'].fillna(-1, inplace=True)
	all_data['ForecastId'].fillna(-1, inplace=True)

	# Aruba has no Lat nor Long. Inform it manually
	all_data.loc[all_data['Lat'].isna()==True, 'Lat'] = 12.510052
	all_data.loc[all_data['Long'].isna()==True, 'Long'] = -70.009354

	return all_data


def addingWolrd(all_data): 
	ts = time.time()
	all_data = calculate_lag(all_data, range(1,7), 'ConfirmedCases')
	all_data = calculate_lag(all_data, range(1,7), 'Fatalities')
	all_data = calculate_trend(all_data, range(1,7), 'ConfirmedCases')
	all_data = calculate_trend(all_data, range(1,7), 'Fatalities')
	all_data.replace([np.inf, -np.inf], 0, inplace=True)
	all_data.fillna(0, inplace=True)
	print("Time spent: ", time.time()-ts)

	# Load countries data file
	world_population = pd.read_csv(world_population_dir)

	# Select desired columns and rename some of them
	world_population = world_population[['Country (or dependency)', 'Population (2020)', 'Density (P/Km²)', 'Land Area (Km²)', 'Med. Age', 'Urban Pop %']]
	world_population.columns = ['Country (or dependency)', 'Population (2020)', 'Density', 'Land Area', 'Med Age', 'Urban Pop']

	# Replace United States by US
	world_population.loc[world_population['Country (or dependency)']=='United States', 'Country (or dependency)'] = 'US'

	# Remove the % character from Urban Pop values
	world_population['Urban Pop'] = world_population['Urban Pop'].str.rstrip('%')

	# Replace Urban Pop and Med Age "N.A" by their respective modes, then transform to int
	world_population.loc[world_population['Urban Pop']=='N.A.', 'Urban Pop'] = int(world_population.loc[world_population['Urban Pop']!='N.A.', 'Urban Pop'].mode()[0])
	world_population['Urban Pop'] = world_population['Urban Pop'].astype('int16')
	world_population.loc[world_population['Med Age']=='N.A.', 'Med Age'] = int(world_population.loc[world_population['Med Age']!='N.A.', 'Med Age'].mode()[0])
	world_population['Med Age'] = world_population['Med Age'].astype('int16')

	all_data = all_data.merge(world_population, left_on='Country/Region', right_on='Country (or dependency)', how='left')
	all_data[['Population (2020)', 'Density', 'Land Area', 'Med Age', 'Urban Pop']] = all_data[['Population (2020)', 'Density', 'Land Area', 'Med Age', 'Urban Pop']].fillna(0)


	# Label encode countries and provinces. Save dictionary for exploration purposes
	all_data.drop('Country (or dependency)', inplace=True, axis=1)
	all_data['Country/Region'] = le.fit_transform(all_data['Country/Region'])
	number_c = all_data['Country/Region']
	countries = le.inverse_transform(all_data['Country/Region'])
	country_dict = dict(zip(countries, number_c)) 
	all_data['Province/State'] = le.fit_transform(all_data['Province/State'])
	number_p = all_data['Province/State']
	province = le.inverse_transform(all_data['Province/State'])
	province_dict = dict(zip(province, number_p)) 

	data = all_data.copy()
	features = ['Id', 'ForecastId', 'Country/Region', 'Province/State', 'ConfirmedCases', 'Fatalities', 
	       'Day_num', 'Day', 'Month', 'Year', 'Long', 'Lat']
	data = data[features]

	# Apply log transformation to all ConfirmedCases and Fatalities columns, except for trends
	data[['ConfirmedCases', 'Fatalities']] = data[['ConfirmedCases', 'Fatalities']].astype('float64')
	data[['ConfirmedCases', 'Fatalities']] = data[['ConfirmedCases', 'Fatalities']].apply(lambda x: np.log(x))

	# Replace infinites
	data.replace([np.inf, -np.inf], 0, inplace=True)



	return (data,country_dict,all_data)



# Split data into train/test
def split_data(data):
    
    # Train set
    x_train = data[data.ForecastId == -1].drop(['ConfirmedCases', 'Fatalities'], axis=1)
    y_train_1 = data[data.ForecastId == -1]['ConfirmedCases']
    y_train_2 = data[data.ForecastId == -1]['Fatalities']

    # Test set
    x_test = data[data.ForecastId != -1].drop(['ConfirmedCases', 'Fatalities'], axis=1)

    # Clean Id columns and keep ForecastId as index
    x_train.drop('Id', inplace=True, errors='ignore', axis=1)
    x_train.drop('ForecastId', inplace=True, errors='ignore', axis=1)
    x_test.drop('Id', inplace=True, errors='ignore', axis=1)
    x_test.drop('ForecastId', inplace=True, errors='ignore', axis=1)
    
    return x_train, y_train_1, y_train_2, x_test


# Linear regression model
def lin_reg(X_train, Y_train, X_test):
    # Create linear regression object
    regr = linear_model.LinearRegression()

    # Train the model using the training sets
    regr.fit(X_train, Y_train)

    # Make predictions using the testing set
    y_pred = regr.predict(X_test)
    
    return regr, y_pred


# Submission function
def get_submission(df, target1, target2):
    
    prediction_1 = df[target1]
    prediction_2 = df[target2]

    # Submit predictions
    prediction_1 = [int(item) for item in list(map(round, prediction_1))]
    prediction_2 = [int(item) for item in list(map(round, prediction_2))]
    
    submission = pd.DataFrame({
        "ForecastId": df['ForecastId'].astype('int32'), 
        "ConfirmedCases": prediction_1, 
        "Fatalities": prediction_2
    })
    submission.to_csv('submission.csv', index=False)


# if __name__ == "__main__":
# 	predict.main()






