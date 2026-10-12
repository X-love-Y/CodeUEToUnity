// 示例 UE 源码: 第三人称角色, 含 WASD 移动 / 跳跃 / 爬墙 / 弹簧臂相机
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "WallClimbComponent.h"
#include "PlayerCharacter.generated.h"

UENUM(BlueprintType)
enum class EMovementState : uint8
{
    Walking     UMETA(DisplayName = "Walking"),
    Sprinting   UMETA(DisplayName = "Sprinting"),
    Jumping     UMETA(DisplayName = "Jumping"),
    Climbing    UMETA(DisplayName = "Climbing")
};

UCLASS()
class MYGAME_API APlayerCharacter : public ACharacter
{
    GENERATED_BODY()

public:
    APlayerCharacter();

    virtual void BeginPlay() override;
    virtual void Tick(float DeltaTime) override;
    virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;

    UFUNCTION(BlueprintPure, Category = "Movement")
    EMovementState GetMovementState() const { return MovementState; }

protected:
    // ---- 组件 ----
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Camera")
    USpringArmComponent* CameraBoom;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Camera")
    UCameraComponent* FollowCamera;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Movement")
    UWallClimbComponent* ClimbComponent;

    // ---- 可调参数 ----
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Movement")
    float WalkSpeed = 400.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Movement")
    float SprintSpeed = 750.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Movement")
    float ClimbSpeed = 200.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Camera")
    float MouseSensitivity = 1.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Camera")
    float MinPitch = -70.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Camera")
    float MaxPitch = 70.0f;

private:
    EMovementState MovementState = EMovementState::Walking;

    // 爬墙时用于保持贴墙的方向
    FVector ClimbSurfaceNormal = FVector::ZeroVector;
    bool bClimbInputHeld = false;

    void MoveForward(float Value);
    void MoveRight(float Value);

    void TurnAtRate(float Rate);
    void LookUpAtRate(float Rate);

    void OnJumpPressed();
    void OnJumpReleased();
    void OnClimbPressed();
    void OnClimbReleased();

    void UpdateMovementState();
    void ApplyWalkMovement(float DeltaTime);
    void ApplyClimbMovement(float DeltaTime);
    void UpdateCameraBoom(float DeltaTime);
};
