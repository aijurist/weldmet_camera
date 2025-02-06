s1 = input("enter String 1:")
s2 = input("enter String 2:")

counter = 0
for i in range(0, len(s1)):
    if(s1[i] == s2[i]):
        counter = counter + 1
        
if(len(s1) - counter == 2):
    print(True)
else:
    print(False)