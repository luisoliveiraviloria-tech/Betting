import difflib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def get_algorithms():
    from src.models.classifiers.decisiontree import DecisionTree
    from src.models.classifiers.discriminant import DiscriminantAnalysisClassifier
    from src.models.classifiers.extremeboosting import XGBoost
    from src.models.classifiers.knn import KNN
    from src.models.classifiers.logistic import LogisticRegressor
    from src.models.classifiers.naivebayes import NaiveBayes
    from src.models.classifiers.randomforest import RandomForest
    from src.models.classifiers.svm import SVM

    return {
        'logistic': LogisticRegressor,
        'discriminant': DiscriminantAnalysisClassifier,
        'decisiontree': DecisionTree,
        'randomforest': RandomForest,
        'xgboost': XGBoost,
        'knn': KNN,
        'naivebayes': NaiveBayes,
        'svm': SVM,
    }


def fuzzy_match_team(team_name: str, known_teams):
    """ Matches a free-text team name against the known teams of a league dataset. """

    matches = difflib.get_close_matches(team_name, known_teams, n=1, cutoff=0.0)
    if not matches:
        return None, 0.0

    best = matches[0]
    ratio = difflib.SequenceMatcher(a=team_name.lower(), b=best.lower()).ratio()
    return best, ratio
